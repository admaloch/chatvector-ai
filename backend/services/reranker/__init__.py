"""Reranker provider factory."""

from __future__ import annotations

import logging
import time

from core.config import config
from db.base import ChunkMatch
from services.reranker.base import (
    RerankRequest,
    RerankResult,
    RerankerProvider,
    RerankerProviderError,
)
from services.reranker.cohere import CohereRerankerProvider
from services.reranker.noop import NoopRerankerProvider
from services.reranker.similarity import SimilarityRerankerProvider

logger = logging.getLogger(__name__)

_provider_cache: dict[str, RerankerProvider] = {}

VALID_RERANKER_PROVIDERS = {"similarity", "cohere"}


def _reset_reranker_provider() -> None:
    """Clear cached providers (for tests)."""
    _provider_cache.clear()


def get_reranker_provider_for_config(
    *,
    reranking_enabled: bool,
    reranker_provider: str,
) -> RerankerProvider:
    """Return a reranker for one request.

    A disabled request returns a Noop provider that is not stored as the
    process-wide provider, so a concurrent request can still enable reranking.
    Real providers are cached by name.
    """
    if not reranking_enabled:
        return NoopRerankerProvider()

    name = (reranker_provider or "").strip().lower()
    cached = _provider_cache.get(name)
    if cached is not None:
        return cached

    if name == "similarity":
        provider: RerankerProvider = SimilarityRerankerProvider()
        logger.info("Using similarity reranker provider")
    elif name == "cohere":
        provider = CohereRerankerProvider()
        logger.info("Using cohere reranker provider")
    else:
        raise ValueError(
            f"Unknown RERANKER_PROVIDER={name!r}. "
            f"Expected one of: {', '.join(sorted(VALID_RERANKER_PROVIDERS))}."
        )

    _provider_cache[name] = provider
    return provider


def get_reranker_provider() -> RerankerProvider:
    """Return the reranker selected by process configuration."""
    return get_reranker_provider_for_config(
        reranking_enabled=config.ENABLE_RERANKING,
        reranker_provider=config.RERANKER_PROVIDER,
    )


async def rerank_chunks_with_debug(
    query: str,
    chunks: list[ChunkMatch],
    *,
    top_k: int | None = None,
    reranking_enabled: bool | None = None,
    reranker_provider: str | None = None,
) -> tuple[list[ChunkMatch], dict | None]:
    """Apply configured reranking and report what ran.

    Returns ``(chunks, reranker_debug)``. ``reranker_debug`` is ``None`` when
    reranking did not run. When the provider fails (timeout, HTTP error, invalid
    response), the pre-rerank order is returned, truncated to ``top_k``, and
    ``reranker_debug["fallback_reason"]`` is set. The request never fails.
    """
    if not chunks:
        return [], None

    enabled = config.ENABLE_RERANKING if reranking_enabled is None else reranking_enabled
    if not enabled:
        return chunks, None

    provider_name = config.RERANKER_PROVIDER if reranker_provider is None else reranker_provider
    provider_name = (provider_name or "").strip().lower()
    provider = get_reranker_provider_for_config(
        reranking_enabled=True,
        reranker_provider=provider_name,
    )
    request = RerankRequest(query=query, candidates=chunks, top_k=top_k)

    started = time.perf_counter()
    try:
        result = await provider.rerank(request)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        if isinstance(exc, RerankerProviderError):
            reason = exc.reason
            model = exc.model
            candidates_sent = exc.candidates_sent
        else:
            reason = f"unexpected_error:{type(exc).__name__}"
            model = None
            candidates_sent = None
        logger.warning(
            "Reranker %r failed (%s); falling back to pre-rerank order",
            provider_name,
            reason,
            exc_info=not isinstance(exc, RerankerProviderError),
        )
        limit = top_k if top_k is not None else len(chunks)
        return chunks[:limit], {
            "provider": provider_name,
            "model": model,
            "latency_ms": latency_ms,
            "candidates_sent": candidates_sent if candidates_sent is not None else 0,
            "fallback_reason": reason,
        }
    latency_ms = int((time.perf_counter() - started) * 1000)

    if result.original_order != result.reranked_order:
        logger.debug(
            "Reranked %d chunk(s): original=%s -> reranked=%s",
            len(result.candidates),
            result.original_order,
            result.reranked_order,
        )
    else:
        logger.debug(
            "Reranking enabled; order unchanged for %d chunk(s): %s",
            len(result.candidates),
            result.reranked_order,
        )

    return result.candidates, {
        "provider": provider_name,
        "model": result.model,
        "latency_ms": latency_ms,
        "candidates_sent": (
            result.candidates_sent if result.candidates_sent is not None else len(chunks)
        ),
    }


async def rerank_chunks_if_enabled(
    query: str,
    chunks: list[ChunkMatch],
    *,
    top_k: int | None = None,
    reranking_enabled: bool | None = None,
    reranker_provider: str | None = None,
) -> list[ChunkMatch]:
    """
    Apply configured reranking after retrieval and before context construction.

    When reranking is disabled, returns chunks unchanged (no top_k truncation).
    ``reranking_enabled`` / ``reranker_provider`` override the process settings
    for this call. ``None`` keeps ``ENABLE_RERANKING`` / ``RERANKER_PROVIDER``.
    """
    reranked, _ = await rerank_chunks_with_debug(
        query,
        chunks,
        top_k=top_k,
        reranking_enabled=reranking_enabled,
        reranker_provider=reranker_provider,
    )
    return reranked
