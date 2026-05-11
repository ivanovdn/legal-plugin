# api/routes/documents.py
"""Document endpoints — ingest, list, get, delete."""

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from api.models import ApiResponse
from ingest.pipeline import ingest_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


@router.post("/ingest", response_model=ApiResponse)
def ingest_file(
    file: UploadFile = File(...),
    client_id: str = Form("internal"),
    jurisdiction: str = Form(""),
    doc_type: str = Form("contract"),
    sensitivity: str = Form("internal"),
    collection: str = Form("legal_docs"),
):
    """Upload and ingest a PDF or DOCX document."""
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Supported: {list(SUPPORTED_EXTENSIONS)}",
        )

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        content = file.file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        chunk_count = ingest_document(
            filepath=tmp_path,
            client_id=client_id,
            jurisdiction=jurisdiction,
            doc_type=doc_type,
            sensitivity=sensitivity,
            collection=collection,
        )
        return ApiResponse(
            status="ok",
            data={
                "filename": filename,
                "chunks": chunk_count,
                "collection": collection,
                "client_id": client_id,
            },
        )
    except Exception as e:
        logger.exception("Ingest failed for %s", filename)
        return ApiResponse(status="error", errors=[str(e)])
    finally:
        tmp_path.unlink(missing_ok=True)
