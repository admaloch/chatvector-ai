from abc import ABC, abstractmethod
from typing import List, Tuple

# abstract base class that defines WHAT database operations we need
# all dbservices (sqlalchemy, supabas, mongodb etc.) must implement these methods.
# This ensures they're interchangeable.

class DatabaseService(ABC):

    # create a document record and returns the document id.
    @abstractmethod
    async def create_document(self, filename: str) -> str:
        pass
    
    #insert multiple chunks and their embeddings return list of ids
    @abstractmethod
    async def insert_chunks_batch(
        self, 
        doc_id: str, 
        chunks_with_embeddings: List[Tuple[str, List[float]]]
    ) -> List[str]:
        pass
    
    #grab a document by id
    @abstractmethod
    async def get_document(self, doc_id: str) -> dict:
        pass
    
