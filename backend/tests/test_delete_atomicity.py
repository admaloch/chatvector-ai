import pytest
import uuid
import asyncio
import sys
from db import get_db_service
from db.base import ChunkRecord

@pytest.mark.asyncio
async def test_delete_document_atomicity_integration():
    """
    Integration test to verify that deleting a document also removes its chunks.
    This verifies atomicity (via transaction in SQLAlchemy or RPC in Supabase).
    """
    db = get_db_service()
    file_name = f"test_atomicity_{uuid.uuid4()}.pdf"
    
    # 1. Create a document
    doc_id = await db.create_document(file_name)
    
    # 2. Store some chunks
    chunk_records = [
        ChunkRecord(
            chunk_text=f"Chunk {i}",
            embedding=[0.1] * 1536, # Placeholder embedding
            chunk_index=i,
            page_number=1,
            character_offset_start=i * 10,
            character_offset_end=(i + 1) * 10
        )
        for i in range(3)
    ]
    await db.store_chunks_with_embeddings(doc_id, chunk_records)
    
    # 3. Verify chunks exist (using a raw query or checking similarity search)
    # For simplicity, we use find_similar_chunks which we know works.
    matches = await db.find_similar_chunks(doc_id, [0.1] * 1536, match_count=10)
    assert len(matches) == 3
    
    # 4. Delete the document
    await db.delete_document(doc_id)
    
    # 5. Verify document is gone
    doc = await db.get_document(doc_id)
    assert doc is None
    
    # 6. Verify chunks are gone (orphans check)
    matches_after = await db.find_similar_chunks(doc_id, [0.1] * 1536, match_count=10)
    assert len(matches_after) == 0
