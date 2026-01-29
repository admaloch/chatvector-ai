# backend/services/db_service.py
import logging
from services.embedding_service import get_embedding
from services.db_service import insert_chunk

logger = logging.getLogger(__name__)

async def ingest_chunks(chunks: list[str], doc_id: str):
    """
    Orchestrates chunk ingestion for a single document.

    - Responsible for embedding generation and per-chunk insertion.
    - Ensures embeddings are stored as plain float lists for pgvector.
    - Partial ingestion may occur if an error is raised; errors are logged.
    """
    inserted_chunk_ids = []
    try:
        for idx, chunk in enumerate(chunks, start=1):
            logger.info(f"Ingesting chunk {idx}/{len(chunks)} for document {doc_id}")

            # Get embedding as plain list
            embedding = await get_embedding(chunk)
            if not isinstance(embedding, list):
                # Safety check: convert any ContentEmbedding object
                try:
                    embedding = embedding.to_list()
                except AttributeError:
                    logger.warning(
                        f"Embedding for chunk {idx} is not a list; "
                        f"defaulting to zero vector"
                    )
                    embedding = [0.0] * 3072

            # Insert chunk with embedding
            chunk_id = await insert_chunk(doc_id, chunk, embedding)
            inserted_chunk_ids.append(chunk_id)

        logger.info(f"Successfully ingested {len(inserted_chunk_ids)} chunks for document {doc_id}")
        return inserted_chunk_ids

    except Exception as e:
        logger.error(f"Chunk ingestion failed for document {doc_id}: {e}")
        raise
