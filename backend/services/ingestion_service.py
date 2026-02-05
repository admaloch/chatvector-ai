# backend/services/db_service.py
import logging
from services.embedding_service import get_embedding
from services.db_service import insert_chunks_batch

logger = logging.getLogger(__name__)

async def ingest_chunks(chunks: list[str], doc_id: str):
    """
    Orchestrates chunk ingestion for a single document.
    - Embeddings generated per chunk
    - Chunks inserted in batch
    """
    try:
        chunks_with_embeddings = []

        for idx, chunk in enumerate(chunks, start=1):
            logger.info(f"Embedding chunk {idx}/{len(chunks)} for document {doc_id}")
            embedding = await get_embedding(chunk)
            chunks_with_embeddings.append((chunk, embedding))

        inserted_chunk_ids = await insert_chunks_batch(
            doc_id, chunks_with_embeddings
        )

        logger.info(
            f"Successfully ingested {len(inserted_chunk_ids)} chunks for document {doc_id}"
        )
        return inserted_chunk_ids

    except Exception as e:
        logger.error(f"Chunk ingestion failed for document {doc_id}: {e}")
        raise

