import logging
from services.embedding_service import get_embeddings
from services.db_service import insert_chunks_batch

logger = logging.getLogger(__name__)

async def ingest_chunks(chunks: list[str], doc_id: str):
    """
    Ingests document chunks: generates embeddings in batch and inserts in batch.
    """
    try:
        logger.info(f"Generating embeddings for {len(chunks)} chunks for document {doc_id}")
        embeddings = await get_embeddings(chunks)
        chunks_with_embeddings = list(zip(chunks, embeddings))
        inserted_chunk_ids = await insert_chunks_batch(doc_id, chunks_with_embeddings)
        logger.info(f"Successfully ingested {len(inserted_chunk_ids)} chunks for document {doc_id}")
        return inserted_chunk_ids
    except Exception as e:
        logger.error(f"Chunk ingestion failed for document {doc_id}: {e}")
        raise


