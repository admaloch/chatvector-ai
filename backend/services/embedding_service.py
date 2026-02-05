import asyncio
from google import genai
from core.config import config
import logging

logger = logging.getLogger(__name__)

# Initialize the GenAI client
client = genai.Client(api_key=config.GEN_AI_KEY)

async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings for multiple chunks at once using Gemini.
    Returns a list of 768-dim vectors.
    """
    for attempt in range(3):
        try:
            logger.info(f"Requesting embeddings for {len(texts)} chunks from GenAI (Attempt {attempt+1}/3)...")
            
            result = await asyncio.to_thread(
                client.models.embed_content,
                model="models/text-embedding-004",
                contents=texts
            )

            embeddings = [e.values for e in result.embeddings]
            logger.info(f"Generated {len(embeddings)} embeddings successfully")
            return embeddings

        except Exception as e:
            wait_time = (attempt + 1) * 2
            logger.error(f"Batch embedding failed (Attempt {attempt + 1}/3): {e}")
            await asyncio.sleep(wait_time)

    logger.error("Failed to get embeddings after 3 attempts. Returning zero vectors")
    return [[0.0]*768 for _ in texts]
