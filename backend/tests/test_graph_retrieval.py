"""Fixture-level GraphRAG prototype: default-off, 1-hop expansion, eval note."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.config import config
from db.base import ChunkMatch
from services import eval_metrics
from services.chat_service import retrieve_for_documents
from services.graph_retrieval import (
    expand_chunks,
    expand_chunks_if_enabled,
    load_fixture_graph,
)
from services.retrieval_config import RetrievalProcessDefaults, resolve_retrieval_config

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "graph" / "multihop.json"


def _chunk(
    document_id: str,
    chunk_index: int,
    *,
    text: str = "seed",
    score: float = 0.9,
) -> ChunkMatch:
    return ChunkMatch(
        id=f"{document_id}:{chunk_index}",
        chunk_text=text,
        document_id=document_id,
        chunk_index=chunk_index,
        similarity=score,
        file_name="billing.txt",
    )


@pytest.fixture
def multihop_graph():
    return load_fixture_graph(FIXTURE_PATH)


def test_load_fixture_graph_reads_entities_and_relations(multihop_graph):
    assert ("graph-doc-01", 0) in multihop_graph.chunk_payloads
    assert ("graph-doc-01", 1) in multihop_graph.chunk_payloads
    assert "refund-policy" in multihop_graph.related_entities["annual-plan"]
    assert "annual-plan" in multihop_graph.related_entities["refund-policy"]


def test_expand_chunks_recovers_one_hop_neighbor(multihop_graph):
    seeds = [
        _chunk(
            "graph-doc-01",
            0,
            text="The annual plan is billed once per year and includes all product tiers.",
        )
    ]
    expanded = expand_chunks(seeds, multihop_graph)

    keys = [(c.document_id, c.chunk_index) for c in expanded]
    assert keys[0] == ("graph-doc-01", 0)
    assert ("graph-doc-01", 1) in keys
    refund = next(c for c in expanded if c.chunk_index == 1)
    assert "Refunds for the annual plan" in refund.chunk_text


def test_expand_chunks_does_not_duplicate_seeds(multihop_graph):
    seeds = [
        _chunk("graph-doc-01", 0),
        _chunk("graph-doc-01", 1, text="already present"),
    ]
    expanded = expand_chunks(seeds, multihop_graph)
    keys = [(c.document_id, c.chunk_index) for c in expanded]
    assert keys == [("graph-doc-01", 0), ("graph-doc-01", 1)]


def test_expand_chunks_empty_graph_is_noop():
    from services.graph_retrieval import EntityChunkGraph

    seeds = [_chunk("graph-doc-01", 0)]
    assert expand_chunks(seeds, EntityChunkGraph()) == seeds


def test_expand_chunks_if_enabled_noop_when_flag_off(monkeypatch, multihop_graph):
    monkeypatch.setattr(config, "ENABLE_GRAPH_RETRIEVAL", False)
    monkeypatch.setattr(config, "GRAPH_RETRIEVAL_FIXTURE_PATH", str(FIXTURE_PATH))
    seeds = [_chunk("graph-doc-01", 0)]
    assert expand_chunks_if_enabled(seeds) == seeds


def test_expand_chunks_if_enabled_noop_without_fixture_path(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GRAPH_RETRIEVAL", True)
    monkeypatch.setattr(config, "GRAPH_RETRIEVAL_FIXTURE_PATH", None)
    seeds = [_chunk("graph-doc-01", 0)]
    assert expand_chunks_if_enabled(seeds) == seeds


def test_expand_chunks_if_enabled_noop_on_invalid_json(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(config, "ENABLE_GRAPH_RETRIEVAL", True)
    monkeypatch.setattr(config, "GRAPH_RETRIEVAL_FIXTURE_PATH", str(bad))
    seeds = [_chunk("graph-doc-01", 0)]
    assert expand_chunks_if_enabled(seeds) == seeds


def test_expand_chunks_if_enabled_loads_fixture_when_flag_on(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GRAPH_RETRIEVAL", True)
    monkeypatch.setattr(config, "GRAPH_RETRIEVAL_FIXTURE_PATH", str(FIXTURE_PATH))
    seeds = [_chunk("graph-doc-01", 0)]
    expanded = expand_chunks_if_enabled(seeds)
    assert ("graph-doc-01", 1) in {(c.document_id, c.chunk_index) for c in expanded}


def test_multihop_eval_note_graph_lifts_recall(multihop_graph):
    """Eval note: seed-only recall@5 is 0; 1-hop expansion recovers the refund chunk."""
    relevant = {("graph-doc-01", 1)}
    seeds = [_chunk("graph-doc-01", 0)]
    seed_keys = [eval_metrics.chunk_match_key(c.document_id, c.chunk_index) for c in seeds]
    expanded = expand_chunks(seeds, multihop_graph)
    expanded_keys = [
        eval_metrics.chunk_match_key(c.document_id, c.chunk_index) for c in expanded
    ]

    assert eval_metrics.recall_at_k(seed_keys, relevant, k=5) == 0.0
    assert eval_metrics.recall_at_k(expanded_keys, relevant, k=5) == 1.0
    assert eval_metrics.mrr(seed_keys, relevant) == 0.0
    assert eval_metrics.mrr(expanded_keys, relevant) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_retrieve_for_documents_unchanged_when_graph_flag_off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GRAPH_RETRIEVAL", False)
    monkeypatch.setattr(config, "ENABLE_RERANKING", False)
    monkeypatch.setattr(config, "HYBRID_RETRIEVAL_ENABLED", False)
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)

    seed = _chunk("graph-doc-01", 0, text="annual plan")
    expand = Mock(side_effect=AssertionError("graph expansion must not run"))
    with (
        patch("services.chat_service.get_embeddings", new=AsyncMock(return_value=[[0.1]])),
        patch("services.chat_service.find_similar_chunks", new=AsyncMock(return_value=[seed])),
        patch("services.graph_retrieval.expand_chunks_if_enabled", expand),
    ):
        effective = resolve_retrieval_config({}, RetrievalProcessDefaults.from_process())
        result = await retrieve_for_documents(
            "What is the refund policy for annual plans?",
            ["graph-doc-01"],
            "dev",
            effective,
        )

    expand.assert_not_called()
    assert [c.id for c in result.chunks] == ["graph-doc-01:0"]


@pytest.mark.asyncio
async def test_retrieve_for_documents_expands_when_graph_flag_on(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GRAPH_RETRIEVAL", True)
    monkeypatch.setattr(config, "GRAPH_RETRIEVAL_FIXTURE_PATH", str(FIXTURE_PATH))
    monkeypatch.setattr(config, "ENABLE_RERANKING", False)
    monkeypatch.setattr(config, "HYBRID_RETRIEVAL_ENABLED", False)
    monkeypatch.setattr(config, "QUERY_TRANSFORMATION_ENABLED", False)

    seed = _chunk(
        "graph-doc-01",
        0,
        text="The annual plan is billed once per year and includes all product tiers.",
    )
    with (
        patch("services.chat_service.get_embeddings", new=AsyncMock(return_value=[[0.1]])),
        patch("services.chat_service.find_similar_chunks", new=AsyncMock(return_value=[seed])),
    ):
        effective = resolve_retrieval_config({}, RetrievalProcessDefaults.from_process())
        result = await retrieve_for_documents(
            "What is the refund policy for annual plans?",
            ["graph-doc-01"],
            "dev",
            effective,
        )

    keys = [(c.document_id, c.chunk_index) for c in result.chunks]
    assert keys[0] == ("graph-doc-01", 0)
    assert ("graph-doc-01", 1) in keys
