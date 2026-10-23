import hashlib
import io
import uuid
from datetime import datetime, timezone
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession

router = APIRouter(tags=["document extraction"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 100
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log", ".xml", ".yaml", ".yml"}


@router.post("/extract")
async def extract_document(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    # Defense in depth: the scoped-session middleware and this handler both
    # validate the server-issued bearer capability. Never allow anonymous uploads.
    authorization = request.headers.get("authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        raise HTTPException(status_code=401, detail="A valid anonymous session is required")
    token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    session = (
        db.query(AnonymousSession)
        .filter(
            AnonymousSession.token_hash == token_hash,
            AnonymousSession.revoked_at.is_(None),
            AnonymousSession.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if session is None:
        raise HTTPException(status_code=401, detail="Anonymous session is invalid or expired")
    filename = (file.filename or "upload").strip()[:255] or "upload"
    suffix = PurePath(filename).suffix.lower()
    content_type = (file.content_type or "").lower()
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()

    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Files must be 10 MiB or smaller")

    source = "text"
    if suffix == ".pdf" or content_type == "application/pdf":
        source = "text"
        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            if len(reader.pages) > MAX_PDF_PAGES:
                raise HTTPException(status_code=413, detail="PDFs are limited to 100 pages")
            extracted = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail="The PDF could not be read") from exc
        if not extracted:
            raise HTTPException(
                status_code=422,
                detail="No embedded text was found. Scanned-image OCR is not available on this API yet; type the key details instead.",
            )
    elif suffix in TEXT_EXTENSIONS or content_type.startswith("text/") or content_type in {
        "application/json", "application/xml"
    }:
        try:
            extracted = data.decode("utf-8-sig").strip()
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="This text file must use UTF-8 encoding") from exc
    else:
        raise HTTPException(
            status_code=415,
            detail="This file type is not supported by the deployed extractor. Use a UTF-8 text file or a text-based PDF; scanned images require OCR, and Office files need a separately deployed extractor.",
        )

    if not extracted:
        raise HTTPException(status_code=422, detail="The file contains no readable text")

    document_id = f"doc_{uuid.uuid4().hex}"
    return {
        "ok": True,
        "document": {
            "id": document_id,
            "name": filename,
            "mime_type": content_type or None,
            "extracted_text": extracted,
            "extracted_text_preview": extracted[:240],
            "extracted_char_count": len(extracted),
            "ocr_confidence": None,
            "extraction_source": source,
        },
    }
