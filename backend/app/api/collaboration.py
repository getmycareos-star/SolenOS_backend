import asyncio
import json
import hashlib
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.care import CareEvent, Caregiver, Person, Evidence as EvidenceModel
from app.models.anonymous_session import AnonymousSession
from app.models.time_location import Appointment
from app.models.collaboration import CommunityPost, CommunityReaction, ConversationSession, ConversationMessage, EvidenceUpload

router = APIRouter(tags=["community and companion"])


class PostCreate(BaseModel):
    caregiver_id: Optional[str] = None
    content: str = Field(min_length=1, max_length=5000)
    post_type: str = Field(default="experience", pattern="^(experience|question)$")
    care_stage: str = Field(default="unspecified", pattern="^(early|moderate|late|unspecified)$")
    tags: list[str] = Field(default_factory=list, max_length=20)


class ReactionCreate(BaseModel):
    reaction_type: str = Field(pattern="^(like|thank_you|support)$")


class CompanionCreate(BaseModel):
    person_id: str
    text: str = Field(min_length=1, max_length=4000)
    session_id: Optional[str] = None


def _scope(request: Request, db: Session) -> tuple[str, str]:
    # Prefer middleware-scoped identity. Fallback resolves the same bearer
    # capability from the database; request-body IDs are never credentials.
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if person_id and caregiver_id:
        return str(person_id), str(caregiver_id)
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS anonymous session is required")
    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Anonymous session is invalid or expired")
    return str(session.person_id), str(session.caregiver_id)


def _post_json(db: Session, post: CommunityPost, actor_id: Optional[str] = None) -> dict:
    reactions = db.query(CommunityReaction).filter(CommunityReaction.post_id == post.id).all()
    counts = {"like": 0, "thank_you": 0, "support": 0}
    for reaction in reactions:
        if reaction.reaction_type in counts:
            counts[reaction.reaction_type] += 1
    return {
        "id": post.id,
        "author_type": "peer_caregiver",
        "author_label": "Anonymous caregiver",
        "author_avatar_seed": "caregiver",
        "title": post.content[:100] if post.post_type == "question" else None,
        "body": post.content,
        "content": post.content,
        "media": [],
        "visibility": post.visibility,
        "care_stage": post.care_stage,
        "tags": json.loads(post.tags_json or "[]"),
        "created_at": post.created_at.isoformat() if post.created_at else datetime.now(timezone.utc).isoformat(),
        "updated_at": post.updated_at.isoformat() if post.updated_at else None,
        "provenance_source": "caregiver_submitted",
        "reaction_counts": counts,
        "comment_count": 0,
        "user_reaction": next((r.reaction_type for r in reactions if actor_id and r.caregiver_id == actor_id), None),
        "moderation_status": post.moderation_status,
    }


@router.post("/upload", status_code=201)
async def upload_evidence(
    request: Request,
    file: UploadFile = File(...),
    person_id: str = Form(...),
    caregiver_id: str = Form(...),
    evidence_type: str = Form("document"),
    extracted_text: str = Form(""),
    db: Session = Depends(get_db),
):
    scoped_person_id, scoped_caregiver_id = _scope(request, db)
    if person_id != scoped_person_id or caregiver_id != scoped_caregiver_id:
        raise HTTPException(status_code=403, detail="Upload identity does not match this care-record session")

    filename = os.path.basename(file.filename or "upload").replace("\r", "").replace("\n", "").strip()[:255] or "upload"
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    mime_by_extension = {
        "txt": "text/plain", "md": "text/markdown", "csv": "text/csv",
        "json": "application/json", "xml": "application/xml", "yaml": "application/yaml",
        "yml": "application/yaml", "pdf": "application/pdf", "jpg": "image/jpeg",
        "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif",
    }
    if mime_type in {"", "application/octet-stream"} and suffix in mime_by_extension:
        mime_type = mime_by_extension[suffix]

    allowed_mime_types = {
        "text/plain", "text/markdown", "text/csv", "application/json", "application/xml",
        "text/xml", "application/yaml", "application/pdf",
        "image/jpeg", "image/png", "image/webp", "image/gif",
    }
    if mime_type not in allowed_mime_types:
        raise HTTPException(
            status_code=415,
            detail="This file type cannot be stored by SolenOS. Use a text file, text-based PDF, JPEG, PNG, WebP, or GIF.",
        )

    data = await file.read(10 * 1024 * 1024 + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Files must be 10 MiB or smaller")

    signatures = {
        "application/pdf": lambda blob: blob.startswith(b"%PDF-"),
        "image/jpeg": lambda blob: blob.startswith(b"\xff\xd8\xff"),
        "image/png": lambda blob: blob.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/gif": lambda blob: blob.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": lambda blob: len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP",
    }
    signature_check = signatures.get(mime_type)
    if signature_check is not None and not signature_check(data):
        raise HTTPException(status_code=422, detail="The file contents do not match the declared file type")
    if mime_type.startswith("text/") or mime_type in {"application/json", "application/xml", "application/yaml"}:
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="Text uploads must use UTF-8 encoding") from exc

    evidence_type = evidence_type.strip().lower()
    if evidence_type not in {"document", "medication_photo", "care_photo"}:
        raise HTTPException(status_code=422, detail="Unsupported evidence type")

    caregiver = db.query(Caregiver).filter_by(id=scoped_caregiver_id, person_id=scoped_person_id).first()
    if not caregiver:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care record")

    # Uploaded text remains source evidence; it is not promoted to a care assertion.
    # The extraction label is client-provided and is recorded as such, never trusted
    # as proof that OCR or review happened on the server.
    bounded_text = extracted_text.strip()[:100_000]
    digest = hashlib.sha256(data).hexdigest()
    evidence = EvidenceModel(
        person_id=scoped_person_id,
        type=evidence_type,
        source_text=bounded_text,
        original_file_path=None,
        uploaded_by_caregiver_id=scoped_caregiver_id,
        extra_metadata=json.dumps({
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": len(data),
            "sha256": digest,
            "text_source": "client_supplied" if bounded_text else "not_available",
        }),
        source_type="caregiver_upload",
        evidence_status="recorded",
    )
    db.add(evidence)
    db.flush()
    db.add(EvidenceUpload(
        evidence_id=evidence.id,
        filename=filename,
        mime_type=mime_type,
        content=data,
        size_bytes=len(data),
        sha256=digest,
    ))
    db.commit()
    return {
        "evidence_id": evidence.id,
        "size_bytes": len(data),
        "mime_type": mime_type,
        "sha256": digest,
        "persisted": True,
    }


@router.get("/upload/{evidence_id}")
def download_evidence(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scope(request, db)
    evidence = db.query(EvidenceModel).filter_by(id=evidence_id, person_id=person_id).first()
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence not found")
    blob = db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).first()
    if not blob:
        raise HTTPException(status_code=404, detail="Original upload is unavailable")
    safe_filename = blob.filename.replace("\r", "").replace("\n", "").replace('"', "")
    return Response(
        content=blob.content,
        media_type=blob.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"', "X-Content-Type-Options": "nosniff"},
    )


@router.get("/community")
def community_feed(request: Request, limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db)):
    _, caregiver_id = _scope(request, db)
    posts = (db.query(CommunityPost)
        .filter(CommunityPost.visibility == "public", CommunityPost.moderation_status == "published")
        .order_by(CommunityPost.created_at.desc()).limit(limit).all())
    return {"identity": "anonymous_caregiver", "posts": [_post_json(db, p, caregiver_id) for p in posts], "count": len(posts)}


@router.post("/community", status_code=201)
def create_community_post(payload: PostCreate, request: Request, db: Session = Depends(get_db)):
    _, caregiver_id = _scope(request, db)
    if payload.caregiver_id and payload.caregiver_id != caregiver_id:
        raise HTTPException(status_code=403, detail="Post attribution does not match this caregiver session")
    if not db.query(Caregiver.id).filter(Caregiver.id == caregiver_id).first():
        raise HTTPException(status_code=403, detail="The session caregiver is not available")
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Post content cannot be blank")
    post = CommunityPost(
        caregiver_id=caregiver_id, author_type="peer_caregiver",
        author_label="Anonymous caregiver", post_type=payload.post_type,
        content=content, care_stage=payload.care_stage,
        tags_json=json.dumps([str(tag).strip().lower()[:40] for tag in payload.tags if str(tag).strip()][:20]),
        visibility="public", moderation_status="published",
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return _post_json(db, post, caregiver_id)


@router.post("/community/{post_id}/reactions")
def react_to_post(post_id: str, payload: ReactionCreate, request: Request, db: Session = Depends(get_db)):
    _, caregiver_id = _scope(request, db)
    post = db.query(CommunityPost).filter_by(id=post_id, visibility="public", moderation_status="published").first()
    if not post:
        raise HTTPException(status_code=404, detail="Published community post not found")
    reaction = db.query(CommunityReaction).filter_by(post_id=post_id, caregiver_id=caregiver_id).first()
    if reaction:
        reaction.reaction_type = payload.reaction_type
    else:
        reaction = CommunityReaction(post_id=post_id, caregiver_id=caregiver_id, reaction_type=payload.reaction_type)
        db.add(reaction)
    db.commit()
    db.refresh(post)
    return _post_json(db, post, caregiver_id)


@router.delete("/community/{post_id}/reactions")
def remove_reaction(post_id: str, request: Request, db: Session = Depends(get_db)):
    _, caregiver_id = _scope(request, db)
    reaction = db.query(CommunityReaction).filter_by(post_id=post_id, caregiver_id=caregiver_id).first()
    if reaction:
        db.delete(reaction)
        db.commit()
    return {"ok": True, "removed": reaction is not None}


def _literal_matches(db: Session, observation: str) -> list[dict]:
    terms = {term.lower() for term in re.findall(r"[a-zA-Z0-9']{3,}", observation)}
    posts = (db.query(CommunityPost)
        .filter(CommunityPost.visibility == "public", CommunityPost.moderation_status == "published")
        .order_by(CommunityPost.created_at.desc()).limit(200).all())
    scored = []
    for post in posts:
        words = {term.lower() for term in re.findall(r"[a-zA-Z0-9']{3,}", post.content + " " + (post.care_stage or "") + " " + (post.tags_json or ""))}
        overlap = terms & words
        if overlap:
            scored.append((len(overlap), post, sorted(overlap)))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [{
        "post_id": post.id, "author_label": "Anonymous caregiver", "author_type": "peer_caregiver",
        "content": post.content, "tags": json.loads(post.tags_json or "[]"),
        "matched_terms": terms_hit, "match_type": "literal_keyword_overlap_not_clinical_advice",
    } for _, post, terms_hit in scored[:4]]


@router.post("/community/experience")
@router.post("/community/experience/summary")
def community_experience(payload: dict, request: Request, db: Session = Depends(get_db)):
    _scope(request, db)
    observation = str(payload.get("observation", "")).strip()
    if not observation:
        raise HTTPException(status_code=422, detail="observation is required")
    matches = _literal_matches(db, observation)
    return {
        "summary": None, "matches": matches, "post_ids": [m["post_id"] for m in matches],
        "provider_status": "not_configured", "ai_generated": False,
        "notice": "These are literal matches from published caregiver posts. No AI summary was generated.",
        "matching_method": "literal_keyword_overlap_not_clinical_advice",
    }


def _ollama_url() -> tuple[str, str]:
    base = os.getenv("OLLAMA_BASE_URL", "").strip().rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b").strip()
    if not base or not model:
        raise HTTPException(status_code=503, detail="Companion model is not configured on the backend")
    parsed = urlparse(base)
    host = (parsed.hostname or "").lower()
    environment = (
        os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or ""
    ).strip().lower()
    if environment in {"production", "prod"}:
        accepted = parsed.scheme == "http" and host.endswith(".railway.internal")
    else:
        accepted = (
            parsed.scheme == "http"
            and (host.endswith(".railway.internal") or host in {"localhost", "127.0.0.1"})
        )
    if not accepted:
        raise HTTPException(status_code=503, detail="Companion model must use the private backend model service; public model endpoints are not accepted")
    return base, model


def _ollama_chat(base: str, model: str, messages: list[dict]) -> str:
    body = json.dumps({"model": model, "messages": messages, "stream": False}).encode("utf-8")
    request = urllib.request.Request(base + "/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Companion model is unavailable; no response was generated") from exc
    content = result.get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(status_code=503, detail="Companion model returned an invalid response")
    return content.strip()


@router.post("/conversation/message")
async def companion_message(payload: CompanionCreate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    if payload.person_id != person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    person = db.query(Person).filter_by(id=person_id).first()
    caregiver = db.query(Caregiver).filter_by(id=caregiver_id, person_id=person_id).first()
    if not person or not caregiver:
        raise HTTPException(status_code=403, detail="Care record session is invalid")
    if payload.session_id:
        session = db.query(ConversationSession).filter_by(id=payload.session_id, person_id=person_id, caregiver_id=caregiver_id, status="active").first()
        if not session:
            raise HTTPException(status_code=404, detail="Active conversation not found for this care record")
    else:
        session = ConversationSession(person_id=person_id, caregiver_id=caregiver_id, title=payload.text.strip()[:80])
        db.add(session)
        # Keep session creation in the same transaction as the first message.
        # If the model is unavailable, no empty conversation is left behind.
        db.flush()
        db.refresh(session)

    lowered = payload.text.lower()
    emergency_terms = ("can't breathe", "cannot breathe", "not breathing", "unconscious", "severe bleeding", "stroke symptoms", "trying to kill myself", "suicidal")
    emergency = any(term in lowered for term in emergency_terms)
    evidence_ids: list[str] = []
    if emergency:
        reply = "This may be an emergency. Contact local emergency services or seek urgent medical help now. I cannot assess or diagnose an emergency through chat."
        verdict = "blocked"
        safety_note = "Possible emergency language detected; urgent human assessment is needed."
    else:
        base, model = _ollama_url()
        recent_events = (db.query(CareEvent).filter(CareEvent.person_id == person_id).order_by(CareEvent.occurred_at.desc()).limit(8).all())
        context_lines = [f"Recorded event: {e.title} — {e.description or 'No additional detail'} ({e.occurred_at.isoformat() if e.occurred_at else 'time unknown'})" for e in recent_events]
        appointments = (db.query(Appointment).filter(Appointment.person_id == person_id).order_by(Appointment.scheduled_at.asc()).limit(5).all())
        context_lines += [f"Recorded appointment: {a.title} — {a.status}" for a in appointments]
        system_prompt = (
            "You are SolenOS Companion, a caregiver-support assistant, not a clinician. "
            "Do not diagnose, prescribe, recommend medication changes, or assert causes from patterns. "
            "Separate recorded facts from uncertainty, ask a focused follow-up when context is missing, "
            "and advise contacting a qualified professional for clinical decisions. "
            "Never claim a fact is in the care record unless it appears in the supplied context. "
            "Community posts are not authoritative care-record facts.\n\n"
            "Authorized care-record context (may be empty):\n" + ("\n".join(context_lines) if context_lines else "No care events or appointments are recorded.")
        )
        prior_messages = (
            db.query(ConversationMessage)
            .filter_by(session_id=session.id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(12)
            .all()
        )
        model_messages = [{"role": "system", "content": system_prompt}]
        model_messages.extend(
            {"role": row.role, "content": row.content}
            for row in reversed(prior_messages)
            if row.role in {"user", "assistant"}
        )
        model_messages.append({"role": "user", "content": payload.text.strip()})
        reply = await asyncio.to_thread(_ollama_chat, base, model, model_messages)
        verdict = "passed"
        safety_note = None

    db.add(ConversationMessage(session_id=session.id, role="user", content=payload.text.strip(), guard_verdict=None, safety_note=None, evidence_ids_json="[]"))
    assistant_row = ConversationMessage(session_id=session.id, role="assistant", content=reply, guard_verdict=verdict, safety_note=safety_note, evidence_ids_json=json.dumps(evidence_ids))
    db.add(assistant_row)
    db.commit()
    db.refresh(assistant_row)
    history = db.query(ConversationMessage).filter_by(session_id=session.id).order_by(ConversationMessage.created_at.asc()).all()
    return {
        "session_id": session.id, "person_id": person_id, "reply": reply,
        "guard_verdict": verdict, "safety_note": safety_note, "confidence": None,
        "evidence_ids": evidence_ids, "ai_generated": not emergency,
        "messages": [{"role": row.role, "text": row.content, "created_at": row.created_at.isoformat() if row.created_at else None, "guard_verdict": row.guard_verdict, "safety_note": row.safety_note} for row in history],
    }


@router.get("/conversation/{session_id}")
def conversation_history(session_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    session = db.query(ConversationSession).filter_by(id=session_id, person_id=person_id, caregiver_id=caregiver_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Conversation not found")
    rows = db.query(ConversationMessage).filter_by(session_id=session.id).order_by(ConversationMessage.created_at.asc()).all()
    return {"session_id": session.id, "person_id": person_id, "messages": [{"role": row.role, "text": row.content, "created_at": row.created_at.isoformat() if row.created_at else None} for row in rows]}
