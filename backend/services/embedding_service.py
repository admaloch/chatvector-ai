import asyncio
from google import genai
from core.config import config
import logging

logger = logging.getLogger(__name__)

# Initialize the GenAI client
client = genai.Client(api_key=config.GEN_AI_KEY)

async def get_embedding(text: str):
    """
    Get an embedding for the provided text using Gemini Embeddings.
    Returns 768-dimensional vectors to match database schema.
    """
    for attempt in range(3):
        try:
            logger.info(f"Requesting embedding from GenAI (Attempt {attempt + 1}/3)...")
            
            # Use text-embedding-004 which returns 768 dimensions
            result = await asyncio.to_thread(
                client.models.embed_content,
                model="models/text-embedding-004",  # 768 dimensions
                contents=text
            )
            
            # Extract the embedding values
            content_embedding = result.embeddings[0]
            embedding_vector = content_embedding.values

            logger.info(f"Generated embedding of length: {len(embedding_vector)}")
            return embedding_vector

        except Exception as e:
            wait_time = (attempt + 1) * 2
            logger.error(f"Embedding generation failed (Attempt {attempt + 1}/3). Error: {str(e)}")
            await asyncio.sleep(wait_time)

    logger.error("Failed to get embedding after 3 attempts. Returning zero vector.")
    # Match database schema: 768 dimensions
    return [0.0] * 768
