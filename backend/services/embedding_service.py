"""Embedding service — thin facade that delegates to the configured provider."""

import logging

from core.config import config, get_embedding_dim
from services.providers import get_embedding_provider
from utils.retry import (
    DEFAULT_BACKOFF,
    DEFAULT_BASE_DELAY,
    DEFAULT_MAX_RETRIES,
    retry_async,
)

logger = logging.getLogger(__name__)

# Auto-detected from the configured EMBEDDING_PROVIDER / EMBEDDING_MODEL.
# Switching provider will require re-embedding stored data.
EMBEDDING_DIM = get_embedding_dim()


async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings for multiple texts.

    Delegates to whichever provider is selected via EMBEDDING_PROVIDER.
    Retry logic is applied at this service layer — providers raise on failure.
    """
    provider = get_embedding_provider()

    async def _embed() -> list[list[float]]:
        logger.info("Requesting embeddings for %d inputs", len(texts))
        return await provider.embed(texts)

    return await retry_async(
        _embed,
        max_retries=DEFAULT_MAX_RETRIES,
        base_delay=DEFAULT_BASE_DELAY,
        backoff=DEFAULT_BACKOFF,
        timeout=float(config.EMBEDDING_HTTP_TIMEOUT_SEC),
        func_name="embedding_service.get_embeddings",
    )


async def get_embedding(text: str) -> list[float]:
    """Convenience wrapper for single-text embedding."""
    return (await get_embeddings([text]))[0]
