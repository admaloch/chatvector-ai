"""Hosted reranker backed by the Cohere rerank API."""

from __future__ import annotations

import asyncio
import math
from dataclasses import replace

import httpx

from core.config import config
from db.base import SCORE_TYPE_RERANKED, ChunkMatch
from services.reranker.base import (
    RerankRequest,
    RerankResult,
    RerankerProvider,
    RerankerProviderError,
)
from services.reranker.similarity import _chunk_key

class CohereRerankerProvider(RerankerProvider):
    """Reorders candidates with Cohere's rerank endpoint.

    Settings are read from ``config`` on every call so a cached instance never
    freezes the API key, timeout, or candidate cap. Any vendor failure raises
    ``RerankerProviderError``; the caller falls back to retrieval order.
    """

    async def rerank(self, request: RerankRequest) -> RerankResult:
        original_order = [_chunk_key(chunk) for chunk in request.candidates]
        model = config.COHERE_RERANK_MODEL
        if not request.candidates:
            return RerankResult(
                candidates=[],
                original_order=original_order,
                reranked_order=[],
                model=model,
                candidates_sent=0,
            )

        api_key = config.COHERE_API_KEY
        if not api_key:
            raise RerankerProviderError("missing_api_key", model=model, candidates_sent=0)

        sent = request.candidates[: config.RERANKER_MAX_CANDIDATES]
        overflow = request.candidates[len(sent) :]
        scores = await self._call_vendor(
            api_key=api_key,
            model=model,
            query=request.query,
            documents=[chunk.chunk_text for chunk in sent],
        )

        ranked_indexes = sorted(scores, key=lambda index: scores[index], reverse=True)
        ordered: list[ChunkMatch] = []
        for rerank_order, index in enumerate(ranked_indexes, start=1):
            score = scores[index]
            ordered.append(
                replace(
                    sent[index],
                    similarity=score,
                    score_type=SCORE_TYPE_RERANKED,
                    reranker_score=score,
                    rerank_order=rerank_order,
                )
            )
        # Candidates the vendor did not score (cap overflow or omitted results)
        # keep their retrieval order after the scored ones.
        scored_indexes = set(scores)
        unscored = [chunk for i, chunk in enumerate(sent) if i not in scored_indexes]
        ordered.extend(unscored)
        ordered.extend(overflow)

        limit = request.top_k if request.top_k is not None else len(ordered)
        ordered = ordered[:limit]
        return RerankResult(
            candidates=ordered,
            original_order=original_order,
            reranked_order=[_chunk_key(chunk) for chunk in ordered],
            model=model,
            candidates_sent=len(sent),
        )

    async def _call_vendor(
        self,
        *,
        api_key: str,
        model: str,
        query: str,
        documents: list[str],
    ) -> dict[int, float]:
        """Return ``{candidate index: relevance score}`` or raise RerankerProviderError."""
        timeout = config.RERANKER_TIMEOUT_SEC
        sent = len(documents)
        payload = {
            "model": model,
            "query": query,
            "documents": documents,
            "top_n": sent,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            # httpx timeouts are per phase; wait_for enforces one overall deadline.
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                response = await asyncio.wait_for(
                    client.post(config.COHERE_RERANK_URL, json=payload, headers=headers),
                    timeout=timeout,
                )
        except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
            raise RerankerProviderError(
                "timeout", model=model, candidates_sent=sent
            ) from exc
        except httpx.HTTPError as exc:
            raise RerankerProviderError(
                f"request_error:{type(exc).__name__}", model=model, candidates_sent=sent
            ) from exc

        if response.status_code != 200:
            raise RerankerProviderError(
                f"http_{response.status_code}", model=model, candidates_sent=sent
            )

        try:
            return _parse_scores(response.json(), candidate_count=sent)
        except (ValueError, TypeError) as exc:
            raise RerankerProviderError(
                f"invalid_response:{exc}", model=model, candidates_sent=sent
            ) from exc


def _parse_scores(body: object, *, candidate_count: int) -> dict[int, float]:
    if not isinstance(body, dict):
        raise ValueError("body is not an object")
    results = body.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("missing results")

    scores: dict[int, float] = {}
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("result is not an object")
        index = item.get("index")
        score = item.get("relevance_score")
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("result index is not an integer")
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
        ):
            raise ValueError("result relevance_score is not a finite number")
        if index < 0 or index >= candidate_count:
            raise ValueError("result index out of range")
        if index in scores:
            raise ValueError("duplicate result index")
        scores[index] = float(score)
    return scores
