from abc import ABC, abstractmethod

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
    async def store_chunks_with_embeddings(
        self, 
        doc_id: str, 
        chunks_with_embeddings: list[tuple[str, list[float]]]
    ) -> list[str]:
        pass
    
    #grab a document by id
    @abstractmethod
    async def get_document(self, doc_id: str) -> dict:
        pass
    
