"""Behavioral tests for per-request retrieval configuration."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core.auth import AuthContext
from core.config import config
from db.base import SCORE_TYPE_HYBRID_RRF, SCORE_TYPE_VECTOR, ChunkMatch
from services.chat_service import (
    answer_question_for_document,
    answer_question_stream_for_document,
    answer_questions_for_documents_batch,
    retrieve_for_documents,
)
from services.query_service import QueryTransformResult
from services.retrieval_config import RetrievalProcessDefaults, resolve_retrieval_config

TEST_AUTH = AuthContext(tenant_id="dev")
PHASE3_ORDER = ("chunk-a", "chunk-b", "chunk-c")


def _chunk(chunk_id: str, text: str, *, index: int, score_type: str = SCORE_TYPE_VECTOR) -> ChunkMatch:
    return ChunkMatch(
        id=chunk_id,
        chunk_text=text,
        document_id="doc-1",
        chunk_index=index,
        similarity=0.5,
        score_type=score_type,
        vector_score=0.5,
        file_name="doc.txt",
    )


def _baseline_chunks() -> list[ChunkMatch]:
    return [
        _chunk(chunk_id, f"text {chunk_id}", index=index)
        for index, chunk_id in enumerate(PHASE3_ORDER)
    ]


@pytest.fixture
def pinned_phase3_env(monkeypatch):
    monkeypatch.setattr(config, "HYBRID_RETRIEVAL_ENABLED", False)
    monkeypatch.setattr(config, "ENABLE_RERANKING", False)
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_STRATEGY", "rewrite")


@pytest.fixture
def passthrough_docs(monkeypatch):
    async def _passthrough(**kwargs):
        return list(kwargs["requested_doc_ids"])

    monkeypatch.setattr(
        "services.chat_service._resolve_retrieval_doc_ids",
        _passthrough,
    )


def _chat_patches(find_similar):
    return (
        patch("services.chat_service.get_embeddings", new=AsyncMock(return_value=[[0.1, 0.2]])),
        patch("services.chat_service.find_similar_chunks", new=find_similar),
        patch("services.chat_service.generate_answer", new=AsyncMock(return_value=("ans", 1, "m"))),
    )


@pytest.mark.asyncio
async def test_omitted_retrieval_config_matches_phase3_chunk_order(pinned_phase3_env, passthrough_docs):
    chunks = _baseline_chunks()
    find_similar = AsyncMock(return_value=chunks)
    embed_patch, find_patch, answer_patch = _chat_patches(find_similar)

    with embed_patch, find_patch, answer_patch:
        omitted = await answer_question_for_document(
            question="fixed question",
            doc_id="doc-1",
            auth=TEST_AUTH,
            retrieval_fields={},
        )
        direct = await answer_question_for_document(
            question="fixed question",
            doc_id="doc-1",
            auth=TEST_AUTH,
        )

    omitted_ids = [source["chunk_index"] for source in omitted["sources"]]
    direct_ids = [source["chunk_index"] for source in direct["sources"]]
    assert omitted_ids == [0, 1, 2]
    assert direct_ids == omitted_ids
    assert omitted["sources"][0]["score_type"] == SCORE_TYPE_VECTOR
    assert "hybrid_enabled" not in find_similar.await_args.kwargs
    assert config.HYBRID_RETRIEVAL_ENABLED is False
    assert config.ENABLE_RERANKING is False


@pytest.mark.asyncio
async def test_hybrid_request_returns_hybrid_rrf_where_vector_baseline_does_not(
    pinned_phase3_env, passthrough_docs
):
    vector_chunks = _baseline_chunks()
    hybrid_chunks = [
        _chunk("hybrid-hit", "keyword hit", index=0, score_type=SCORE_TYPE_HYBRID_RRF)
    ]

    async def fake_find(**kwargs):
        if kwargs.get("hybrid_enabled"):
            return hybrid_chunks
        return vector_chunks

    find_similar = AsyncMock(side_effect=fake_find)
    embed_patch, find_patch, answer_patch = _chat_patches(find_similar)
    with embed_patch, find_patch, answer_patch:
        baseline = await answer_question_for_document(
            question="fixed question",
            doc_id="doc-1",
            auth=TEST_AUTH,
            retrieval_fields={},
        )
        hybrid = await answer_question_for_document(
            question="fixed question",
            doc_id="doc-1",
            auth=TEST_AUTH,
            retrieval_fields={"hybrid_enabled": True},
        )

    assert all(source["score_type"] != SCORE_TYPE_HYBRID_RRF for source in baseline["sources"])
    assert any(source["score_type"] == SCORE_TYPE_HYBRID_RRF for source in hybrid["sources"])
    assert config.HYBRID_RETRIEVAL_ENABLED is False


@pytest.mark.asyncio
async def test_reranking_request_changes_rerank_order(pinned_phase3_env, passthrough_docs):
    chunks = [
        _chunk("low", "unrelated text about cats", index=0),
        _chunk("high", "beta query terms appear here", index=1),
    ]
    find_similar = AsyncMock(return_value=list(chunks))
    embed_patch, find_patch, answer_patch = _chat_patches(find_similar)
    with embed_patch, find_patch, answer_patch:
        disabled = await answer_question_for_document(
            question="beta query",
            doc_id="doc-1",
            auth=TEST_AUTH,
            retrieval_fields={"reranking_enabled": False},
        )
        enabled = await answer_question_for_document(
            question="beta query",
            doc_id="doc-1",
            auth=TEST_AUTH,
            retrieval_fields={"reranking_enabled": True, "reranker_provider": "similarity"},
        )

    assert [source["chunk_index"] for source in disabled["sources"]] == [0, 1]
    assert all(source.get("rerank_order") is None for source in disabled["sources"])
    assert [source["rerank_order"] for source in enabled["sources"]] == [1, 2]
    assert enabled["sources"][0]["chunk_index"] == 1
    assert config.ENABLE_RERANKING is False


@pytest.mark.asyncio
async def test_hybrid_keyword_search_uses_effective_retrieval_query(pinned_phase3_env, passthrough_docs):
    seen_queries: list[str] = []

    async def fake_find(**kwargs):
        seen_queries.append(kwargs.get("query_text"))
        return _baseline_chunks()

    async def fake_rewrite(question: str) -> str:
        return "effective keyword query"

    find_similar = AsyncMock(side_effect=fake_find)
    embed_patch, find_patch, answer_patch = _chat_patches(find_similar)
    with (
        embed_patch,
        find_patch,
        answer_patch,
        patch("services.query_service.rewrite_query", new=fake_rewrite),
    ):
        await answer_question_for_document(
            question="raw question about widgets",
            doc_id="doc-1",
            auth=TEST_AUTH,
            retrieval_fields={
                "hybrid_enabled": True,
                "query_transformation_strategy": "rewrite",
            },
        )

    assert seen_queries == ["effective keyword query"]
    assert "raw question about widgets" not in seen_queries
    assert find_similar.await_args.kwargs["hybrid_enabled"] is True


@pytest.mark.asyncio
async def test_concurrent_requests_keep_independent_hybrid_flags(pinned_phase3_env, passthrough_docs):
    seen: list[bool | None] = []

    async def fake_find(**kwargs):
        await asyncio.sleep(0.01)
        seen.append(kwargs.get("hybrid_enabled"))
        return _baseline_chunks()

    find_similar = AsyncMock(side_effect=fake_find)
    embed_patch, find_patch, answer_patch = _chat_patches(find_similar)
    with embed_patch, find_patch, answer_patch:
        await asyncio.gather(
            answer_question_for_document(
                question="one",
                doc_id="doc-1",
                auth=TEST_AUTH,
                retrieval_fields={"hybrid_enabled": True},
            ),
            answer_question_for_document(
                question="two",
                doc_id="doc-1",
                auth=TEST_AUTH,
                retrieval_fields={"hybrid_enabled": False},
            ),
        )

    assert set(seen) == {True, False}
    assert config.HYBRID_RETRIEVAL_ENABLED is False


@pytest.mark.asyncio
async def test_retrieve_for_documents_does_not_call_answer_generation(pinned_phase3_env, passthrough_docs):
    effective = resolve_retrieval_config({}, RetrievalProcessDefaults.from_process())
    generate = AsyncMock(side_effect=AssertionError("answer generation must not run"))
    with (
        patch("services.chat_service.get_embeddings", new=AsyncMock(return_value=[[0.1]])),
        patch("services.chat_service.find_similar_chunks", new=AsyncMock(return_value=_baseline_chunks())),
        patch("services.chat_service.generate_answer", new=generate),
        patch("services.answer_service.generate_answer", new=generate),
    ):
        result = await retrieve_for_documents(
            "fixed question",
            ["doc-1"],
            "dev",
            effective,
        )

    generate.assert_not_called()
    assert [chunk.id for chunk in result.chunks] == list(PHASE3_ORDER)
    assert result.primary_query == "fixed question"
    assert result.context
    assert "total" in result.timing_ms


@pytest.mark.asyncio
async def test_debug_retrieval_includes_effective_config_and_query(pinned_phase3_env, passthrough_docs):
    find_similar = AsyncMock(return_value=_baseline_chunks())
    embed_patch, find_patch, answer_patch = _chat_patches(find_similar)
    with embed_patch, find_patch, answer_patch:
        result = await answer_question_for_document(
            question="fixed question",
            doc_id="doc-1",
            auth=TEST_AUTH,
            debug_retrieval=True,
            retrieval_fields={"hybrid_enabled": True},
        )

    debug = result["retrieval_debug"]
    assert debug["effective_retrieval_query"] == "fixed question"
    assert debug["effective_config"]["hybrid_enabled"] == {"value": True, "source": "request"}
    assert debug["effective_config"]["reranking_enabled"]["source"] == "env"
    assert debug["effective_config"]["max_context_chars"]["source"] == "env"
    assert "timing_ms" in debug


@pytest.mark.asyncio
async def test_compare_style_batch_skips_transform_history_unless_overridden(pinned_phase3_env, passthrough_docs):
    history = [{"role": "user", "content": "earlier topic"}]
    captured: list[list | None] = []

    async def fake_transform(question: str, history=None, **kwargs):
        captured.append(history)
        return QueryTransformResult(queries=[question], original_query=question)

    with (
        patch(
            "services.chat_service.get_embeddings",
            new=AsyncMock(side_effect=lambda texts, **kwargs: [[0.2] for _ in texts]),
        ),
        patch("services.chat_service.find_similar_chunks", new=AsyncMock(return_value=[])),
        patch("services.chat_service.generate_answer", new=AsyncMock(return_value=("ans", 1, "m"))),
        patch("services.chat_service.transform_query", new=fake_transform),
        patch("db.get_session_history", new=AsyncMock(return_value=history)),
        patch("db.store_chat_turn", new=AsyncMock()),
    ):
        await answer_questions_for_documents_batch(
            [{"question": "follow up?", "doc_ids": ["doc-1"], "session_id": "sess-1"}],
            auth=TEST_AUTH,
        )
        await answer_questions_for_documents_batch(
            [
                {
                    "question": "follow up?",
                    "doc_ids": ["doc-1"],
                    "session_id": "sess-1",
                    "retrieval_fields": {"skip_history_transform": False},
                }
            ],
            auth=TEST_AUTH,
        )

    assert captured[0] is None
    assert captured[1] == history


@pytest.mark.asyncio
async def test_stream_and_batch_honor_request_hybrid_flag(pinned_phase3_env, passthrough_docs):
    seen: list[bool | None] = []

    async def fake_find(**kwargs):
        seen.append(kwargs.get("hybrid_enabled"))
        return _baseline_chunks()

    async def fake_stream(question, context):
        yield "token"

    provider = AsyncMock()
    provider.model_name = "test-model"
    find_similar = AsyncMock(side_effect=fake_find)
    with (
        patch(
            "services.chat_service.get_embeddings",
            new=AsyncMock(side_effect=lambda texts, **kwargs: [[0.1] for _ in texts]),
        ),
        patch("services.chat_service.find_similar_chunks", new=find_similar),
        patch("services.chat_service.generate_answer", new=AsyncMock(return_value=("ans", 1, "m"))),
        patch("services.chat_service.generate_answer_stream", new=fake_stream),
        patch("services.providers.get_llm_provider", return_value=provider),
    ):
        async for _event in answer_question_stream_for_document(
            "stream q",
            "doc-1",
            auth=TEST_AUTH,
            retrieval_fields={"hybrid_enabled": True},
        ):
            pass
        await answer_questions_for_documents_batch(
            [
                {
                    "question": "batch q",
                    "doc_ids": ["doc-a", "doc-b"],
                    "retrieval_fields": {"hybrid_enabled": False},
                }
            ],
            auth=TEST_AUTH,
        )

    assert True in seen
    assert False in seen
