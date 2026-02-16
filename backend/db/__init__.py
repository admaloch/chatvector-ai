
import logging
from app.config import config

logger = logging.getLogger(__name__)

# This will hold our chosen database service
db_service = None

# factory func that returns the appropriate database service based on environment
def get_db_service():
    global db_service
    
    if db_service is not None:
        return db_service
    
    if config.APP_ENV.lower() == "development":
        from app.db.sqlalchemy_service import SQLAlchemyService
        db_service = SQLAlchemyService()
        logger.info("Using SQLAlchemy database service (development)")
    else:
        from app.db.supabase_service import SupabaseService
        db_service = SupabaseService()
        logger.info("Using Supabase database service (production)")
    
    return db_service

# Convenience exports - these will always point to the correct implementation
async def create_document(filename: str) -> str:
    """Create a document (uses current environment's database)"""
    service = get_db_service()
    return await service.create_document(filename)

async def insert_chunks_batch(doc_id: str, chunks_with_embeddings: List[Tuple[str, List[float]]]) -> List[str]:
    """Insert chunks (uses current environment's database)"""
    service = get_db_service()
    return await service.insert_chunks_batch(doc_id, chunks_with_embeddings)

async def get_document(doc_id: str) -> dict:
    """Get document (uses current environment's database)"""
    service = get_db_service()
    return await service.get_document(doc_id)

# You can also export the service directly if you prefer
__all__ = [
    "get_db_service",
    "create_document", 
    "insert_chunks_batch",
    "get_document",
    "db_service",  
]