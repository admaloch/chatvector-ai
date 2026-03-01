import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from services.ingestion_pipeline import IngestionPipeline, UploadPipelineError

logger = logging.getLogger(__name__)
router = APIRouter()
ingestion_pipeline = IngestionPipeline()

def _http_error(
    status_code: int,
    code: str,
    stage: str,
    message: str,
    document_id: str | None = None,
) -> HTTPException:
    detail = {
        "code": code,
        "stage": stage,
        "message": message,
    }
    if document_id:
        detail["document_id"] = document_id
    return HTTPException(status_code=status_code, detail=detail)

@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    try:
        return await ingestion_pipeline.process_document(file)

    except UploadPipelineError as e:
        raise _http_error(
            status_code=e.status_code,
            code=e.code,
            stage=e.stage,
            message=e.message,
            document_id=getattr(e, "document_id", None),
        )
