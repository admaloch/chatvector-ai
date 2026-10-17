"""Tests for the hosted (Cohere) reranker provider and its fallback behavior."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core.config import config
from db.base import ChunkMatch
from services.reranker import (
    _reset_reranker_provider,
    get_reranker_provider,
    get_reranker_provider_for_config,
    rerank_chunks_with_debug,
)
from services.reranker.base import RerankRequest, RerankerProviderError
from services.reranker.cohere import CohereRerankerProvider
from services.reranker.noop import NoopRerankerProvider
from services.reranker.similarity import SimilarityRerankerProvider

_URL = "https://api.cohere.test/v2/rerank"
_SECRET = "co-secret-key-123"


def _chunk(chunk_id: str, text: str, *, similarity: float = 0.5) -> ChunkMatch:
    return ChunkMatch(
        id=chunk_id,
        chunk_text=text,
        document_id="doc-1",
        chunk_index=int(chunk_id[-1]) if chunk_id[-1].isdigit() else 0,
        similarity=similarity,
    )


def _vendor_response(results: list, status: int = 200) -> httpx.Response:
    # json.dumps (allow_nan) lets tests send NaN, which real vendors should not.
    return httpx.Response(
        status,
        content=json.dumps({"results": results}).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", _URL),
    )


@pytest.fixture(autouse=True)
def cohere_env(monkeypatch):
    _reset_reranker_provider()
    monkeypatch.setattr(config, "ENABLE_RERANKING", False)
    monkeypatch.setattr(config, "RERANKER_PROVIDER", "similarity")
    monkeypatch.setattr(config, "COHERE_API_KEY", _SECRET)
    monkeypatch.setattr(config, "COHERE_RERANK_MODEL", "rerank-test")
    monkeypatch.setattr(config, "COHERE_RERANK_URL", _URL)
    monkeypatch.setattr(config, "RERANKER_TIMEOUT_SEC", 2.0)
    monkeypatch.setattr(config, "RERANKER_MAX_CANDIDATES", 50)
    yield
    _reset_reranker_provider()


def _post_patch(**kwargs):
    return patch.object(httpx.AsyncClient, "post", new=AsyncMock(**kwargs))


@pytest.mark.asyncio
async def test_vendor_scores_reorder_candidates_against_similarity_baseline():
    chunks = [
        _chunk("c0", "beta query terms appear here", similarity=0.9),
        _chunk("c1", "unrelated text about cats", similarity=0.5),
        _chunk("c2", "middle ground", similarity=0.4),
    ]
    request = RerankRequest(query="beta query", candidates=chunks, top_k=3)
    baseline = await SimilarityRerankerProvider().rerank(request)

    vendor = [
        {"index": 1, "relevance_score": 0.99},
        {"index": 2, "relevance_score": 0.5},
        {"index": 0, "relevance_score": 0.1},
    ]
    with _post_patch(return_value=_vendor_response(vendor)) as post:
        result = await CohereRerankerProvider().rerank(request)

    assert baseline.reranked_order[0] == "c0"
    assert result.reranked_order == ["c1", "c2", "c0"]
    assert result.reranked_order != baseline.reranked_order
    assert [c.rerank_order for c in result.candidates] == [1, 2, 3]
    assert [c.reranker_score for c in result.candidates] == [0.99, 0.5, 0.1]
    assert result.candidates[0].score_type == "reranked"
    assert result.candidates[0].similarity == 0.99
    assert result.model == "rerank-test"
    assert result.candidates_sent == 3

    call = post.await_args
    assert call.args[0] == _URL
    assert call.kwargs["headers"]["Authorization"] == f"Bearer {_SECRET}"
    body = call.kwargs["json"]
    assert body["model"] == "rerank-test"
    assert body["query"] == "beta query"
    assert body["documents"] == [c.chunk_text for c in chunks]
    assert body["top_n"] == 3
    # The vendor key travels only in the header, never in the body.
    assert _SECRET not in str(body)


@pytest.mark.asyncio
async def test_candidate_cap_limits_chunks_sent_and_keeps_overflow_order(monkeypatch):
    monkeypatch.setattr(config, "RERANKER_MAX_CANDIDATES", 2)
    chunks = [_chunk(f"c{i}", f"text {i}") for i in range(4)]
    vendor = [
        {"index": 1, "relevance_score": 0.9},
        {"index": 0, "relevance_score": 0.2},
    ]
    with _post_patch(return_value=_vendor_response(vendor)) as post:
        result = await CohereRerankerProvider().rerank(
            RerankRequest(query="q", candidates=chunks, top_k=None)
        )

    assert post.await_args.kwargs["json"]["documents"] == ["text 0", "text 1"]
    assert post.await_args.kwargs["json"]["top_n"] == 2
    assert result.candidates_sent == 2
    assert result.reranked_order == ["c1", "c0", "c2", "c3"]


@pytest.mark.asyncio
async def test_top_k_truncates_reranked_candidates():
    chunks = [_chunk(f"c{i}", f"text {i}") for i in range(3)]
    vendor = [
        {"index": 2, "relevance_score": 0.9},
        {"index": 1, "relevance_score": 0.5},
        {"index": 0, "relevance_score": 0.1},
    ]
    with _post_patch(return_value=_vendor_response(vendor)):
        result = await CohereRerankerProvider().rerank(
            RerankRequest(query="q", candidates=chunks, top_k=2)
        )
    assert result.reranked_order == ["c2", "c1"]


@pytest.mark.asyncio
async def test_unscored_candidates_follow_scored_ones_in_retrieval_order():
    chunks = [_chunk(f"c{i}", f"text {i}") for i in range(3)]
    vendor = [{"index": 2, "relevance_score": 0.9}]
    with _post_patch(return_value=_vendor_response(vendor)):
        result = await CohereRerankerProvider().rerank(
            RerankRequest(query="q", candidates=chunks, top_k=3)
        )
    assert result.reranked_order == ["c2", "c0", "c1"]


@pytest.mark.asyncio
async def test_empty_candidates_make_no_vendor_call():
    with _post_patch() as post:
        result = await CohereRerankerProvider().rerank(
            RerankRequest(query="q", candidates=[], top_k=3)
        )
    assert result.candidates == []
    assert result.candidates_sent == 0
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_api_key_raises_without_http_call(monkeypatch):
    monkeypatch.setattr(config, "COHERE_API_KEY", None)
    with _post_patch() as post:
        with pytest.raises(RerankerProviderError) as excinfo:
            await CohereRerankerProvider().rerank(
                RerankRequest(query="q", candidates=[_chunk("c0", "t")], top_k=1)
            )
    assert excinfo.value.reason == "missing_api_key"
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_httpx_timeout_maps_to_timeout_reason():
    with _post_patch(side_effect=httpx.ReadTimeout("slow")):
        with pytest.raises(RerankerProviderError) as excinfo:
            await CohereRerankerProvider().rerank(
                RerankRequest(query="q", candidates=[_chunk("c0", "t")], top_k=1)
            )
    assert excinfo.value.reason == "timeout"
    assert excinfo.value.candidates_sent == 1
    assert excinfo.value.model == "rerank-test"


@pytest.mark.asyncio
async def test_hung_vendor_hits_overall_deadline(monkeypatch):
    monkeypatch.setattr(config, "RERANKER_TIMEOUT_SEC", 0.1)

    async def hang(*args, **kwargs):
        await asyncio.sleep(30)

    with patch.object(httpx.AsyncClient, "post", new=hang):
        started = asyncio.get_running_loop().time()
        with pytest.raises(RerankerProviderError) as excinfo:
            await CohereRerankerProvider().rerank(
                RerankRequest(query="q", candidates=[_chunk("c0", "t")], top_k=1)
            )
        elapsed = asyncio.get_running_loop().time() - started

    assert excinfo.value.reason == "timeout"
    assert elapsed < 5


@pytest.mark.asyncio
async def test_transport_error_and_http_status_map_to_reasons():
    request = RerankRequest(query="q", candidates=[_chunk("c0", "t")], top_k=1)
    with _post_patch(side_effect=httpx.ConnectError("boom")):
        with pytest.raises(RerankerProviderError) as excinfo:
            await CohereRerankerProvider().rerank(request)
    assert excinfo.value.reason == "request_error:ConnectError"

    with _post_patch(return_value=_vendor_response([], status=429)):
        with pytest.raises(RerankerProviderError) as excinfo:
            await CohereRerankerProvider().rerank(request)
    assert excinfo.value.reason == "http_429"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "results",
    [
        [],
        [{"index": 5, "relevance_score": 0.5}],
        [{"index": -1, "relevance_score": 0.5}],
        [{"index": 0, "relevance_score": 0.5}, {"index": 0, "relevance_score": 0.4}],
        [{"index": "0", "relevance_score": 0.5}],
        [{"index": 0, "relevance_score": "high"}],
        [{"index": 0, "relevance_score": float("nan")}],
        ["not-an-object"],
    ],
)
async def test_invalid_vendor_results_raise(results):
    request = RerankRequest(
        query="q", candidates=[_chunk("c0", "a"), _chunk("c1", "b")], top_k=2
    )
    with _post_patch(return_value=_vendor_response(results)):
        with pytest.raises(RerankerProviderError) as excinfo:
            await CohereRerankerProvider().rerank(request)
    assert excinfo.value.reason.startswith("invalid_response")


@pytest.mark.asyncio
async def test_non_json_body_raises_invalid_response():
    response = httpx.Response(200, content=b"<html>", request=httpx.Request("POST", _URL))
    with _post_patch(return_value=response):
        with pytest.raises(RerankerProviderError) as excinfo:
            await CohereRerankerProvider().rerank(
                RerankRequest(query="q", candidates=[_chunk("c0", "t")], top_k=1)
            )
    assert excinfo.value.reason.startswith("invalid_response")


@pytest.mark.asyncio
async def test_error_reasons_never_contain_api_key():
    request = RerankRequest(query="q", candidates=[_chunk("c0", "t")], top_k=1)
    with _post_patch(side_effect=httpx.ConnectError(f"failed with {_SECRET}")):
        with pytest.raises(RerankerProviderError) as excinfo:
            await CohereRerankerProvider().rerank(request)
    assert _SECRET not in excinfo.value.reason


def test_cohere_is_selectable_by_name_and_cached():
    first = get_reranker_provider_for_config(reranking_enabled=True, reranker_provider="cohere")
    second = get_reranker_provider_for_config(reranking_enabled=True, reranker_provider=" Cohere ")
    assert isinstance(first, CohereRerankerProvider)
    assert first is second


@pytest.mark.asyncio
async def test_failure_falls_back_to_pre_rerank_order_with_debug_reason():
    chunks = [_chunk(f"c{i}", f"text {i}") for i in range(4)]
    with _post_patch(side_effect=httpx.ReadTimeout("slow")):
        result, debug = await rerank_chunks_with_debug(
            "q",
            chunks,
            top_k=3,
            reranking_enabled=True,
            reranker_provider="cohere",
        )

    assert [c.id for c in result] == ["c0", "c1", "c2"]
    assert all(c.rerank_order is None for c in result)
    assert debug is not None
    assert debug["fallback_reason"] == "timeout"
    assert debug["provider"] == "cohere"
    assert debug["model"] == "rerank-test"
    assert debug["candidates_sent"] == 4
    assert isinstance(debug["latency_ms"], int)


@pytest.mark.asyncio
async def test_unexpected_provider_exception_also_falls_back():
    chunks = [_chunk("c0", "a"), _chunk("c1", "b")]
    provider = CohereRerankerProvider()
    with patch.object(provider, "rerank", new=AsyncMock(side_effect=RuntimeError("boom"))):
        with patch(
            "services.reranker.get_reranker_provider_for_config", return_value=provider
        ):
            result, debug = await rerank_chunks_with_debug(
                "q", chunks, top_k=2, reranking_enabled=True, reranker_provider="cohere"
            )

    assert [c.id for c in result] == ["c0", "c1"]
    assert debug["fallback_reason"] == "unexpected_error:RuntimeError"
    assert debug["candidates_sent"] == 0


@pytest.mark.asyncio
async def test_missing_key_falls_back_instead_of_failing(monkeypatch):
    monkeypatch.setattr(config, "COHERE_API_KEY", None)
    chunks = [_chunk("c0", "a"), _chunk("c1", "b")]
    result, debug = await rerank_chunks_with_debug(
        "q", chunks, top_k=2, reranking_enabled=True, reranker_provider="cohere"
    )
    assert [c.id for c in result] == ["c0", "c1"]
    assert debug["fallback_reason"] == "missing_api_key"


@pytest.mark.asyncio
async def test_success_debug_block_has_provider_metadata():
    chunks = [_chunk("c0", "a"), _chunk("c1", "b")]
    vendor = [
        {"index": 1, "relevance_score": 0.8},
        {"index": 0, "relevance_score": 0.2},
    ]
    with _post_patch(return_value=_vendor_response(vendor)):
        result, debug = await rerank_chunks_with_debug(
            "q", chunks, top_k=2, reranking_enabled=True, reranker_provider="cohere"
        )

    assert [c.id for c in result] == ["c1", "c0"]
    assert debug is not None
    assert debug["provider"] == "cohere"
    assert debug["model"] == "rerank-test"
    assert debug["candidates_sent"] == 2
    assert isinstance(debug["latency_ms"], int)
    assert "fallback_reason" not in debug


@pytest.mark.asyncio
async def test_disabled_reranking_returns_no_debug_block():
    chunks = [_chunk("c0", "a"), _chunk("c1", "b")]
    with _post_patch() as post:
        result, debug = await rerank_chunks_with_debug(
            "q", chunks, top_k=1, reranking_enabled=False, reranker_provider="cohere"
        )
    assert result == chunks
    assert debug is None
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_disabled_request_enabled_reranks_single_request():
    chunks = [_chunk("c0", "a"), _chunk("c1", "b")]
    vendor = [
        {"index": 1, "relevance_score": 0.8},
        {"index": 0, "relevance_score": 0.2},
    ]
    assert config.ENABLE_RERANKING is False
    with _post_patch(return_value=_vendor_response(vendor)):
        enabled, _ = await rerank_chunks_with_debug(
            "q", chunks, top_k=2, reranking_enabled=True, reranker_provider="cohere"
        )
    plain, _ = await rerank_chunks_with_debug("q", chunks, top_k=2)

    assert [c.id for c in enabled] == ["c1", "c0"]
    assert [c.id for c in plain] == ["c0", "c1"]
    assert config.ENABLE_RERANKING is False
    assert isinstance(get_reranker_provider(), NoopRerankerProvider)


@pytest.mark.asyncio
async def test_concurrent_requests_with_opposing_settings_do_not_leak():
    chunks = [_chunk("c0", "a"), _chunk("c1", "b")]
    vendor = [
        {"index": 1, "relevance_score": 0.8},
        {"index": 0, "relevance_score": 0.2},
    ]

    async def slow_post(*args, **kwargs):
        await asyncio.sleep(0.05)
        return _vendor_response(vendor)

    with patch.object(httpx.AsyncClient, "post", new=slow_post):
        outcomes = await asyncio.gather(
            rerank_chunks_with_debug(
                "q", chunks, top_k=2, reranking_enabled=True, reranker_provider="cohere"
            ),
            rerank_chunks_with_debug("q", chunks, top_k=2, reranking_enabled=False),
            rerank_chunks_with_debug(
                "q", chunks, top_k=2, reranking_enabled=True, reranker_provider="cohere"
            ),
            rerank_chunks_with_debug("q", chunks, top_k=2, reranking_enabled=False),
        )

    hosted_a, noop_a, hosted_b, noop_b = outcomes
    assert [c.id for c in hosted_a[0]] == ["c1", "c0"]
    assert [c.id for c in hosted_b[0]] == ["c1", "c0"]
    assert [c.id for c in noop_a[0]] == ["c0", "c1"]
    assert [c.id for c in noop_b[0]] == ["c0", "c1"]
    assert noop_a[1] is None and noop_b[1] is None
    assert hosted_a[1]["provider"] == "cohere"
    assert isinstance(
        get_reranker_provider_for_config(reranking_enabled=False, reranker_provider="cohere"),
        NoopRerankerProvider,
    )
