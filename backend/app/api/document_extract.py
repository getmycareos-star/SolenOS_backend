import hashlib
from datetime import datetime, timezone
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.services.document_extraction import extract_document as extract_document_bytes

router = APIRouter(tags=["document extraction"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post("/extract")
async def extract_document(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Preview extraction from an upload; durable extraction is repeated from saved bytes."""
    authorization = request.headers.get("authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        raise HTTPException(status_code=401, detail="A valid anonymous session is required")
    token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Anonymous session is invalid or expired")

    filename = PurePath(file.filename or "upload").name.replace("\r", "").replace("\n", "").strip()[:255] or "upload"
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Files must be 10 MiB or smaller")

    result = extract_document_bytes(filename, content_type, data)
    if not result["text"].strip():
        raise HTTPException(
            status_code=422,
            detail="No readable text was extracted. The original can still be saved; review it manually.",
        )
    ocr_confidences = [
        page.get("mean_word_confidence")
        for page in result["pages"]
        if isinstance(page.get("mean_word_confidence"), (int, float))
    ]
    mean_confidence = (
        round(sum(ocr_confidences) / len(ocr_confidences), 2)
        if ocr_confidences else None
    )
    return {
        "ok": True,
        "document": {
            "id": "doc_preview",
            "name": filename,
            "mime_type": content_type or None,
            "extracted_text": result["text"],
            "extracted_text_preview": result["text"][:240],
            "extracted_char_count": len(result["text"]),
            "ocr_confidence": mean_confidence,
            "extraction_source": result["method"],
            "pages": result["pages"],
            "truncated": result["truncated"],
            "clinical_confidence": None,
            "review_required": True,
            "written_to_lcr": False,
        },
    }
