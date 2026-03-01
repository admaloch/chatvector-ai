from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from routes.upload import upload
from services.ingestion_pipeline import UploadPipelineError


@pytest.mark.asyncio
async def test_upload_route_delegates_to_ingestion_pipeline():
    mock_file = AsyncMock(spec=UploadFile)
    payload = {
        "message": "Uploaded",
        "document_id": "doc-1",
        "chunks": 2,
        "status": "completed",
        "status_endpoint": "/documents/doc-1/status",
    }

    with patch(
        "routes.upload.ingestion_pipeline.process_document",
        new=AsyncMock(return_value=payload),
    ) as mock_process:
        result = await upload(mock_file)

    assert result == payload
    mock_process.assert_awaited_once_with(mock_file)


@pytest.mark.asyncio
async def test_upload_route_maps_pipeline_error_to_http_exception():
    mock_file = AsyncMock(spec=UploadFile)

    with patch(
        "routes.upload.ingestion_pipeline.process_document",
        new=AsyncMock(
            side_effect=UploadPipelineError(
                status_code=422,
                code="no_text_extracted",
                stage="extracting",
                message="No extractable text was found in the uploaded document.",
                document_id="doc-err",
            )
        ),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await upload(mock_file)

    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["code"] == "no_text_extracted"
    assert excinfo.value.detail["stage"] == "extracting"
    assert excinfo.value.detail["document_id"] == "doc-err"
