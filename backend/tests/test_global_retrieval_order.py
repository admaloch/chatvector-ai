"""Global order and bound for multi-document retrieve_for_documents."""

from unittest.mock import AsyncMock, patch

import pytest

from core.config import config
from db.base import SCORE_TYPE_HYBRID_RRF, SCORE_TYPE_VECTOR, ChunkMatch
from services.chat_service import retrieve_for_documents
from services.retrieval_config import RetrievalProcessDefaults, resolve_retrieval_config


def _chunk(
    document_id: str,
    chunk_index: int,
    *,
    similarity: float | None,
    score_type: str = SCORE_TYPE_VECTOR,
    text: str | None = None,
) -> ChunkMatch:
    return ChunkMatch(
        id=f"{document_id}:{chunk_index}",
        chunk_text=text or f"{document_id} chunk {chunk_index}",
        document_id=document_id,
        chunk_index=chunk_index,
        similarity=similarity,
        score_type=score_type,
        vector_score=similarity if score_type == SCORE_TYPE_VECTOR else None,
        rrf_score=similarity if score_type == SCORE_TYPE_HYBRID_RRF else None,
        file_name=f"{document_id}.txt",
    )


def _effective(**fields):
    return resolve_retrieval_config(fields, RetrievalProcessDefaults.from_process())


def _find_by_doc(chunks_by_doc: dict[str, list[ChunkMatch]]):
    async def fake_find(**kwargs):
        return list(chunks_by_doc[kwargs["doc_id"]])

    return AsyncMock(side_effect=fake_find)


@pytest.fixture
def retrieval_env(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_RERANKING", False)
    monkeypatch.setattr(config, "HYBRID_RETRIEVAL_ENABLED", False)
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "ENABLE_GRAPH_RETRIEVAL", False)


async def _retrieve(document_ids, chunks_by_doc, *, fields=None, **kwargs):
    find_similar = _find_by_doc(chunks_by_doc)
    with (
        patch("services.chat_service.get_embeddings", new=AsyncMock(return_value=[[0.1]])),
        patch("services.chat_service.find_similar_chunks", new=find_similar),
    ):
        result = await retrieve_for_documents(
            "which document is more relevant",
            document_ids,
            "dev",
            _effective(**(fields or {"reranking_enabled": False, "match_count": 5})),
            **kwargs,
        )
    return result, find_similar


@pytest.mark.asyncio
async def test_higher_score_outranks_later_document_when_reranking_off(retrieval_env):
    later_high = _chunk("doc-b", 0, similarity=0.95)
    earlier_low = _chunk("doc-a", 0, similarity=0.40)
    result, _ = await _retrieve(
        ["doc-a", "doc-b"],
        {"doc-a": [earlier_low], "doc-b": [later_high]},
    )

    assert [chunk.id for chunk in result.chunks] == ["doc-b:0", "doc-a:0"]


@pytest.mark.asyncio
async def test_equal_scores_break_by_document_id_not_request_order(retrieval_env):
    doc_a = _chunk("doc-a", 1, similarity=0.70)
    doc_b = _chunk("doc-b", 0, similarity=0.70)
    chunks_by_doc = {"doc-a": [doc_a], "doc-b": [doc_b]}

    first, _ = await _retrieve(["doc-b", "doc-a"], chunks_by_doc)
    second, _ = await _retrieve(["doc-a", "doc-b"], chunks_by_doc)

    expected = ["doc-a:1", "doc-b:0"]
    assert [chunk.id for chunk in first.chunks] == expected
    assert [chunk.id for chunk in second.chunks] == expected


@pytest.mark.asyncio
async def test_equal_scores_break_by_chunk_index_within_document(retrieval_env):
    later_index = _chunk("doc-a", 3, similarity=0.55)
    earlier_index = _chunk("doc-a", 1, similarity=0.55)
    result, _ = await _retrieve(
        ["doc-a"],
        {"doc-a": [later_index, earlier_index]},
    )

    assert [chunk.id for chunk in result.chunks] == ["doc-a:1", "doc-a:3"]


@pytest.mark.asyncio
async def test_single_document_distinct_scores_keep_membership_and_order(retrieval_env):
    chunks = [
        _chunk("doc-a", 0, similarity=0.90),
        _chunk("doc-a", 1, similarity=0.70),
        _chunk("doc-a", 2, similarity=0.40),
    ]
    result, _ = await _retrieve(["doc-a"], {"doc-a": chunks})

    assert [chunk.id for chunk in result.chunks] == ["doc-a:0", "doc-a:1", "doc-a:2"]


@pytest.mark.asyncio
async def test_missing_similarity_sorts_last(retrieval_env):
    missing = _chunk("doc-a", 0, similarity=None)
    scored = _chunk("doc-b", 0, similarity=0.10)
    result, _ = await _retrieve(
        ["doc-a", "doc-b"],
        {"doc-a": [missing], "doc-b": [scored]},
    )

    assert [chunk.id for chunk in result.chunks] == ["doc-b:0", "doc-a:0"]


@pytest.mark.asyncio
async def test_non_rerank_path_cuts_to_match_count_before_context(retrieval_env):
    chunks_by_doc = {
        "doc-a": [
            _chunk("doc-a", 0, similarity=0.50),
            _chunk("doc-a", 1, similarity=0.40),
        ],
        "doc-b": [
            _chunk("doc-b", 0, similarity=0.90),
            _chunk("doc-b", 1, similarity=0.80),
        ],
        "doc-c": [
            _chunk("doc-c", 0, similarity=0.30),
            _chunk("doc-c", 1, similarity=0.20),
        ],
    }
    seen_context_input: list[list[str]] = []

    def capture_context(chunks, session_context=None, **kwargs):
        seen_context_input.append([chunk.id for chunk in chunks])
        from services.context_service import build_context_from_chunks

        return build_context_from_chunks(
            chunks, session_context=session_context, **kwargs
        )

    find_similar = _find_by_doc(chunks_by_doc)
    with (
        patch("services.chat_service.get_embeddings", new=AsyncMock(return_value=[[0.1]])),
        patch("services.chat_service.find_similar_chunks", new=find_similar),
        patch("services.chat_service.build_context_from_chunks", side_effect=capture_context),
    ):
        result = await retrieve_for_documents(
            "which document is more relevant",
            ["doc-a", "doc-b", "doc-c"],
            "dev",
            _effective(reranking_enabled=False, match_count=2),
        )

    assert [chunk.id for chunk in result.chunks] == ["doc-b:0", "doc-b:1"]
    assert seen_context_input == [["doc-b:0", "doc-b:1"]]
    assert all(call.kwargs["match_count"] == 2 for call in find_similar.await_args_list)
    assert find_similar.await_count == 3
