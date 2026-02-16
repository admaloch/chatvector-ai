"""
Integration tests for the upload flow with retry logic.
Tests the complete pipeline with mocked failures.
"""
import pytest
from unittest.mock import AsyncMock, patch

from routes.upload import upload
from fastapi import UploadFile

@pytest.mark.asyncio
async def test_upload_with_retry_success():
    """Test upload succeeds after retryable failures."""
    mock_file = AsyncMock(spec=UploadFile)
    mock_file.filename = "test.pdf"
    mock_file.content_type = "application/pdf"

    # Force development mode so SQLAlchemyService is used
    with patch('core.config.config.APP_ENV', 'development'):
        # Mock the service method
        with patch('db.sqlalchemy_service.SQLAlchemyService.create_document') as mock_db_create:
            mock_db_create.side_effect = [
                Exception("connection timeout"),  # Transient
                "doc123"                          # Success
            ]
            
            with patch('routes.upload.store_chunks_with_embeddings', return_value=["chunk1", "chunk2"]):
                with patch('routes.upload.extract_text_from_file', return_value="sample text"):
                    with patch('routes.upload.get_embeddings', return_value=[[0.1]*3072]):
                        
                        # Mock is_transient_error to return True for our test
                        with patch('utils.retry.is_transient_error', return_value=True):
                            result = await upload(mock_file)
    
    assert result["document_id"] == "doc123"
    assert result["chunks"] == 2
    assert mock_db_create.call_count == 2

@pytest.mark.asyncio
async def test_upload_fails_on_permanent_error():
    """Test upload fails fast on permanent errors."""
    mock_file = AsyncMock(spec=UploadFile)
    mock_file.content_type = "application/pdf"
    mock_file.filename = "test.pdf"

    with patch('core.config.config.APP_ENV', 'development'):
        with patch('db.sqlalchemy_service.SQLAlchemyService.create_document') as mock_db_create:
            mock_db_create.side_effect = Exception("constraint violation")  # Permanent
            
            with patch('routes.upload.extract_text_from_file', return_value="sample text"):
                with patch('routes.upload.get_embeddings', return_value=[[0.1]*3072]):
                    
                    with pytest.raises(Exception, match="constraint violation"):
                        await upload(mock_file)
    
    assert mock_db_create.call_count == 1