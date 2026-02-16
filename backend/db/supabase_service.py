import logging
from app.db.base import DatabaseService
from app.core.supabase_client import supabase_client  

logger = logging.getLogger(__name__)

# Supabase implementation - used in production
class SupabaseService(DatabaseService):    
    async def create_document(self, filename: str) -> str:
        result = supabase_client.table("documents").insert({
            "filename": filename,
            "status": "processing"
        }).execute()
        
        doc_id = result.data[0]["id"]
        logger.info(f"[Supabase] Created document {doc_id}")
        return doc_id
    
    async def store_chunks_with_embeddings(
        self, 
        doc_id: str, 
        chunks_with_embeddings: list[tuple[str, list[float]]]
    ) -> list[str]:
        payload = [
            {
                "document_id": doc_id,
                "chunk_text": chunk_text,
                "embedding": embedding,
            }
            for chunk_text, embedding in chunks_with_embeddings
        ]
        
        result = supabase_client.table("document_chunks").insert(payload).execute()
        chunk_ids = [row["id"] for row in result.data]
        
        logger.info(f"[Supabase] Inserted {len(chunk_ids)} chunks for document {doc_id}")
        return chunk_ids
    
    async def get_document(self, doc_id: str) -> dict:
        result = supabase_client.table("documents").select("*").eq("id", doc_id).execute()
        if result.data:
            return result.data[0]
        return None