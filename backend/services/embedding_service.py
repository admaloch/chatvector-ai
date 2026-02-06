import asyncio
from google import genai
from core.config import config
import logging

logger = logging.getLogger(__name__)

# Initialize the GenAI client
client = genai.Client(api_key=config.GEN_AI_KEY)

async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings for multiple texts.
    Always returns a list of vectors.
    """
    for attempt in range(3):
        try:
            logger.info(
                f"Requesting embeddings for {len(texts)} inputs "
                f"(Attempt {attempt + 1}/3)"
            )

            result = await asyncio.to_thread(
                client.models.embed_content,
                model="models/text-embedding-004",
                contents=texts,
            )

            embeddings = [e.values for e in result.embeddings]
            return embeddings

        except Exception as e:
            wait_time = (attempt + 1) * 2
            logger.error(f"Embedding batch failed: {e}")
            await asyncio.sleep(wait_time)

    logger.error("Embedding batch failed after retries; returning zero vectors")
    return [[0.0] * 768 for _ in texts]

async def get_embedding(text: str) -> list[float]:
    """
    Convenience wrapper for single-text embedding.
    """
    return (await get_embeddings([text]))[0]
