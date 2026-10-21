import json
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.care import Caregiver, Evidence, Person
from app.models.uploads import StoredUpload

router = APIRouter(tags=["uploads"])
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


@router.post("/upload")
async def upload_image_evidence(
    file: UploadFile = File(...),
    caregiver_id: str = Form(...),
    person_id: str = Form(...),
    db: Session = Depends(get_db),
):
    """Persist a compressed image and create the evidence record it belongs to."""
    person = db.query(Person).filter(Person.id == person_id).first()
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    caregiver = (
        db.query(Caregiver)
        .filter(Caregiver.id == caregiver_id, Caregiver.person_id == person_id)
        .first()
    )
    if caregiver is None:
        raise HTTPException(
            status_code=400,
            detail="Caregiver is not linked to the specified person",
        )

    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Only supported image files can be uploaded")

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 2 MB upload limit")

    filename = os.path.basename(file.filename or "care-image").replace('"', "")
    upload = StoredUpload(
        id=str(uuid.uuid4()),
        person_id=person_id,
        caregiver_id=caregiver_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        content=content,
    )

    try:
        db.add(upload)
        db.flush()
        evidence = Evidence(
            person_id=person_id,
            type="document",
            source_text="Image upload",
            original_file_path=f"db://stored-uploads/{upload.id}",
            uploaded_by_caregiver_id=caregiver_id,
            extra_metadata=json.dumps({
                "upload_id": upload.id,
                "filename": filename,
                "content_type": content_type,
                "size_bytes": len(content),
            }),
            source_type="document",
            evidence_status="ACTIVE",
            confidence=1.0,
        )
        db.add(evidence)
        db.flush()
        upload.evidence_id = evidence.id
        db.commit()
        return {
            "evidence_id": evidence.id,
            "file_id": upload.id,
            "filename": filename,
            "size_bytes": len(content),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        await file.close()


@router.get("/upload/{file_id}")
def get_uploaded_image(file_id: str, db: Session = Depends(get_db)):
    upload = db.query(StoredUpload).filter(StoredUpload.id == file_id).first()
    if upload is None:
        raise HTTPException(status_code=404, detail="Uploaded image not found")
    return Response(
        content=upload.content,
        media_type=upload.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{upload.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
