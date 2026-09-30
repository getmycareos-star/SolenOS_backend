"""Conversation, including the parts that are not about care.

Every other module here is a specialist that answers questions. This one is the
thing a caregiver actually types into, and most of what they type is not a
question about medication.

The drift policy is the substance of it. A caregiver mid-crisis who is being
asked a structured question will leave, and a system that keeps steering them
back is a system they stop opening. So the conversation is a peer, not a form:

  in_care_context       answer from the record, with the unknowns attached
  drifting              follow them away, do not steer back
  out_of_care_context   be a normal conversational partner, or say you do not know
  returning             pick the thread up where it was, without recapping

Stage is inferred from the message and the history, then stored, because the
policy only works over time. Knowing someone has been off caring for an hour is
what lets the return be a return rather than a restart.

Two things are refused regardless of stage. Venting is not an opening to hand
over a plan, and the system does not offer false comfort, because a caregiver
who says they are exhausted and is told there is nothing to worry about has been
told something untrue at the moment they most needed it to be true.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.care_context_enums import ConversationStage, SpeakerRole
from app.core.llm_enums import LLMReasoningMode, LLMTask
from app.models.conversation import ConversationSession, ConversationTurn
from app.models.llm import LLMReasoningResult
from app.services.care_common import (
    days_between,
    ensure_utc,
    evidence_map,
    unique,
    utc_now,
)
from app.services.llm import LocalReasoningEngine
from app.services.llm.trust_guard import TrustGuard

# A session is off caring after this many consecutive turns away from care
# context. Below it, the thread is still live and returning is a resumption.
_OFF_CONTEXT_TURNS = 2
# A long silence means a new start, not a return.
_RETURNABLE_AFTER = 21

_VENTING_MARKERS = (
    "exhausted",
    "can't cope",
    "cant cope",
    "i'm done",
    "im done",
    "so tired",
    "fed up",
    "no idea what i'm doing",
    "don't know what i'm doing",
    "alone in this",
    "nobody understands",
    "crying",
    "want to give up",
    "burnt out",
    "burned out",
)

_OUT_OF_CARE_MARKERS = (
    "what's the weather",
    "the weather",
    "how are you",
    "thanks",
    "thank you",
    "cheers",
    "good morning",
    "good night",
    "hello",
    "hi there",
    "weekend",
    "my day",
    "my own",
    "random question",
    "unrelated",
    "another question",
)

_CARE_MARKERS = (
    "medication",
    "medicine",
    "pill",
    "dose",
    "appointment",
    "sleep",
    "eating",
    "fell",
    "fall",
    "confus",
    "memory",
    "wandering",
    "toilet",
    "bath",
    "shower",
    "agitat",
    "pain",
    "hospital",
    "gp",
    "doctor",
    "nurse",
    "care plan",
    "what should",
    "should i",
)

_EMERGENCY_MARKERS = (
    "can't breathe",
    "cannot breathe",
    "chest pain",
    "unconscious",
    "not breathing",
    "severe bleeding",
    "overdose",
    "suicidal",
    "wants to end",
    "blue lips",
    "having a stroke",
    "heart attack",
)


class ConversationEngine:
    def __init__(self, db: Session, engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self.engine = engine or LocalReasoningEngine(db)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def open_session(
        self, person_id: str, caregiver_id: Optional[str] = None
    ) -> ConversationSession:
        active = (
            self.db.query(ConversationSession)
            .filter(
                ConversationSession.person_id == person_id,
                ConversationSession.is_active.is_(True),
            )
            .order_by(ConversationSession.updated_at.desc().nullslast())
            .first()
        )
        if active is not None:
            return active
        session = ConversationSession(
            person_id=person_id,
            caregiver_id=caregiver_id,
            stage=ConversationStage.IN_CARE_CONTEXT,
        )
        self.db.add(session)
        self.db.flush()
        return session

    def get_session(self, session_id: str) -> Optional[ConversationSession]:
        return (
            self.db.query(ConversationSession)
            .filter(ConversationSession.id == session_id)
            .first()
        )

    def list_turns(self, session_id: str, limit: int = 20) -> List[ConversationTurn]:
        return (
            self.db.query(ConversationTurn)
            .filter(ConversationTurn.session_id == session_id)
            .order_by(ConversationTurn.turn_index.desc())
            .limit(limit)
            .all()
        )[::-1]

    # ------------------------------------------------------------------
    # The exchange
    # ------------------------------------------------------------------

    def respond(
        self,
        person_id: str,
        text: str,
        session_id: Optional[str] = None,
        caregiver_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Answer whatever was actually said.

        The caregiver's words are stored exactly as typed, before any
        interpretation, and the reply is stored separately. Trust rule 3.
        """
        if not text or not text.strip():
            raise ValueError("A message is required")

        session = self.get_session(session_id) if session_id else self.open_session(person_id, caregiver_id)
        if session is None:
            session = self.open_session(person_id, caregiver_id)

        stage = self._infer_stage(session, text)
        evidence_ids = self._relevant_evidence(person_id, stage)
        texts = evidence_map(self.db, evidence_ids)

        # The caregiver's turn, stored verbatim before anything is inferred.
        self._record_turn(
            session=session,
            person_id=person_id,
            role=SpeakerRole.CAREGIVER,
            text=text.strip(),
            stage=stage,
            evidence_ids=None,
        )

        safety_note = self._emergency_note(text)
        reply_payload = self.engine.reason(
            task=LLMTask.CONVERSATION_REPLY,
            person_id=person_id,
            subject_type="conversation",
            subject_id=session.id,
            payload={
                "caregiver_said": text.strip(),
                "inferred_stage": stage.value,
                "previous_stage": _enum_value(session.stage),
                "turns_out_of_care_context": session.out_of_context_turns,
                "last_care_topic": session.last_care_topic,
                "recent_turns": [
                    {"role": _enum_value(turn.role), "text": turn.text} for turn in self.list_turns(session.id, 8)
                ],
                "what_is_open_in_the_record": _open_items_view(self, person_id),
                "evidence": texts,
            },
            evidence_ids=evidence_ids,
            evidence_texts=texts,
            mode=LLMReasoningMode.LLM_DRAFT,
        )

        reply_text, grounded, model_context, verdict, qualifications, confidence = self._guard_reply(
            reply_payload, evidence_ids, text
        )
        reply_text = self._apply_stage_floors(reply_text, stage, safety_note)

        if model_context is None and stage == ConversationStage.IN_CARE_CONTEXT:
            possible_context = "No local reasoning available, so this is a fallback rather than an answer."
        else:
            possible_context = model_context

        self._record_turn(
            session=session,
            person_id=person_id,
            role=SpeakerRole.SOLENOS,
            text=reply_text,
            stage=stage,
            evidence_ids=grounded,
            possible_context=possible_context,
            confidence=confidence,
            guard_verdict=verdict,
            guard_qualifications=qualifications,
            safety_note=safety_note,
            reasoning_result_id=reply_payload.id if reply_payload is not None else None,
        )

        self._update_session(session, stage)
        self.db.flush()

        return {
            "session_id": session.id,
            "person_id": person_id,
            "reply": reply_text,
            "stage": stage,
            "evidence_ids": grounded,
            "possible_context": possible_context,
            "confidence": confidence,
            "guard_verdict": verdict,
            "safety_note": safety_note,
            "reasoned_locally": reply_payload is not None,
        }

    def _guard_reply(
        self,
        reply_payload: Optional[LLMReasoningResult],
        permitted: List[str],
        original: str,
    ) -> tuple:
        """Screen the reply, and keep only the part that survives."""
        if reply_payload is None:
            return _fallback_reply(original), [], None, "unavailable", None, None

        guard = TrustGuard(permitted_evidence_ids=permitted, require_evidence=False)
        guarded = guard.guard_conversation(_to_dict(reply_payload))

        if not guarded.is_usable:
            return (
                _fallback_reply(original),
                [],
                "The drafted reply was rejected by the safety rules, so a plain answer was given instead.",
                guarded.verdict.value,
                guarded.qualifications or None,
                None,
            )

        payload = guarded.payload
        return (
            str(payload.get("reply") or "").strip(),
            guarded.output_evidence_ids,
            payload.get("possible_context"),
            guarded.verdict.value,
            guarded.qualifications or None,
            guarded.confidence,
        )

    def _apply_stage_floors(
        self, reply: str, stage: ConversationStage, safety_note: Optional[str]
    ) -> str:
        """Enforce what must be true of a reply at each stage.

        Cheap, deterministic, and it catches the failure the model prompt alone
        does not: a reply that is technically grounded but behaves like a form
        at two in the morning.
        """
        if not reply:
            return _fallback_reply(None, stage=stage)

        lowered = reply.lower()

        if stage in (ConversationStage.DRIFTING, ConversationStage.OUT_OF_CARE_CONTEXT):
            if len(reply) > 400:
                reply = reply[:397].rsplit(" ", 1)[0] + "..."
            if _looks_like_a_form(reply):
                return _fallback_reply(None, stage=stage)

        if stage == ConversationStage.OUT_OF_CARE_CONTEXT and any(
            marker in lowered for marker in ("the record", "the evidence", "is not established", "not established")
        ):
            # Out of care context, do not sound like a system.
            return _fallback_reply(None, stage=stage)

        if safety_note and safety_note.lower() not in lowered:
            reply = f"{reply}\n\n{safety_note}"
        return reply

    # ------------------------------------------------------------------
    # Drift policy
    # ------------------------------------------------------------------

    def _infer_stage(self, session: ConversationSession, text: str) -> ConversationStage:
        """Where is this message, in the conversation?

        Venting is treated as drifting rather than as a care question, because
        a caregiver describing their own exhaustion is not asking for a plan
        and answering with one is the wrong response to a real thing.
        """
        lowered = text.lower()
        away = session.out_of_context_turns or 0
        last_care = ensure_utc(session.last_care_at)
        quiet_days = days_between(last_care, utc_now()) if last_care else None

        if quiet_days is not None and quiet_days > _RETURNABLE_AFTER:
            return ConversationStage.RETURNING if _has_care_language(lowered) else ConversationStage.OUT_OF_CARE_CONTEXT

        if any(marker in lowered for marker in _VENTING_MARKERS):
            return ConversationStage.DRIFTING if _has_care_language(lowered) else ConversationStage.DRIFTING

        if _has_care_language(lowered):
            if away >= _OFF_CONTEXT_TURNS:
                return ConversationStage.RETURNING
            return ConversationStage.IN_CARE_CONTEXT

        if any(marker in lowered for marker in _OUT_OF_CARE_MARKERS):
            return ConversationStage.OUT_OF_CARE_CONTEXT

        if away >= _OFF_CONTEXT_TURNS:
            return ConversationStage.OUT_OF_CARE_CONTEXT
        return ConversationStage.DRIFTING

    def _update_session(self, session: ConversationSession, stage: ConversationStage) -> None:
        session.stage = stage
        session.turn_count = (session.turn_count or 0) + 1
        if stage in (ConversationStage.OUT_OF_CARE_CONTEXT, ConversationStage.DRIFTING):
            session.out_of_context_turns = (session.out_of_context_turns or 0) + 1
        else:
            session.out_of_context_turns = 0
            session.last_care_at = utc_now()
        if stage in (ConversationStage.IN_CARE_CONTEXT, ConversationStage.RETURNING):
            session.last_care_topic = session.current_topic
        session.updated_at = utc_now()

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------

    def _relevant_evidence(self, person_id: str, stage: ConversationStage) -> List[str]:
        """Only pull evidence when there is a care question to ground.

        Small talk does not get the record thrown at it, both because it would
        be absurd and because the more context a model is given, the more it
        will reach for.
        """
        if stage in (ConversationStage.OUT_OF_CARE_CONTEXT, ConversationStage.DRIFTING):
            return []
        from app.models.care import Evidence

        rows = (
            self.db.query(Evidence)
            .filter(Evidence.person_id == person_id)
            .order_by(Evidence.uploaded_at.desc())
            .limit(12)
            .all()
        )
        return unique([row.id for row in rows])

    def _emergency_note(self, text: str) -> Optional[str]:
        lowered = text.lower()
        if any(marker in lowered for marker in _EMERGENCY_MARKERS):
            return (
                "This sounds like an emergency. Contact emergency services now rather than relying on "
                "anything recorded here."
            )
        return None

    def _record_turn(
        self,
        session: ConversationSession,
        person_id: str,
        role: Any,
        text: str,
        stage: ConversationStage,
        evidence_ids: Optional[List[str]] = None,
        possible_context: Optional[str] = None,
        confidence: Optional[float] = None,
        guard_verdict: Optional[str] = None,
        guard_qualifications: Optional[List[str]] = None,
        reasoning_result_id: Optional[str] = None,
        safety_note: Optional[str] = None,
    ) -> ConversationTurn:
        turn = ConversationTurn(
            session_id=session.id,
            person_id=person_id,
            turn_index=session.turn_count or 0,
            role=role,
            text=text,
            stage=stage,
            evidence_ids=evidence_ids,
            possible_context=possible_context,
            confidence=confidence,
            guard_verdict=guard_verdict,
            guard_qualifications=guard_qualifications,
            reasoning_result_id=reasoning_result_id,
            safety_note=safety_note,
        )
        self.db.add(turn)
        self.db.flush()
        return turn

    def session_view(self, session_id: str) -> Dict[str, Any]:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError("Conversation session not found")
        turns = self.list_turns(session_id)
        return {
            "session": session,
            "stage": _enum_value(session.stage),
            "turn_count": session.turn_count,
            "out_of_context_turns": session.out_of_context_turns,
            "last_care_topic": session.last_care_topic,
            "turns": [
                {
                    "role": _enum_value(turn.role),
                    "text": turn.text,
                    "stage": _enum_value(turn.stage) if turn.stage else None,
                    "evidence_ids": turn.evidence_ids or [],
                    "guard_verdict": turn.guard_verdict,
                    "safety_note": turn.safety_note,
                }
                for turn in turns
            ],
        }


_FORM_MARKERS = (
    "bullet",
    "1.",
    "2.",
    "3.",
    "here are",
    "summary:",
    "note:",
    "however,",
    "additionally",
    "furthermore",
    "it is important to note",
    "please consider",
    "as an ai",
    "i recommend",
)


def _looks_like_a_form(text: str) -> bool:
    lowered = text.lower()
    if any(marker in lowered for marker in _FORM_MARKERS):
        return True
    return text.count("\n") >= 3


def _has_care_language(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _CARE_MARKERS)


def _fallback_reply(original: Optional[str], stage: Optional[ConversationStage] = None) -> str:
    """Something honest to say when there is no usable drafted reply.

    Never an apology for being a system, never a promise, and never a reassurance.

    The wording distinguishes "the local model did not answer" from "the record
    has nothing". Those are completely different situations for a caregiver, and
    collapsing them into one sentence made a system outage look like a gap in the
    record - which is the more alarming of the two and the one that was false.
    """
    if stage in (None, ConversationStage.IN_CARE_CONTEXT, ConversationStage.RETURNING):
        return (
            "I could not reach my local reasoning just now, so I have not answered this. "
            "Nothing has been added to the record."
        )
    return "I could not give you an answer to that just now."


def _to_dict(result: LLMReasoningResult) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "reply": result.summary,
        "grounded_claims": result.evidence_ids or [],
        "possible_context": result.possible_context,
        "confidence": result.confidence,
    }
    for item in result.detail or []:
        if isinstance(item, dict) and item.get("field") == "stage":
            payload["stage"] = item.get("value")
    return payload


def _open_items_view(engine: ConversationEngine, person_id: str) -> List[Dict[str, Any]]:
    from app.models.follow_up import FollowUp
    from app.core.care_context_enums import FollowUpStatus

    try:
        rows = (
            engine.db.query(FollowUp)
            .filter(
                FollowUp.person_id == person_id,
                FollowUp.status == FollowUpStatus.SCHEDULED,
            )
            .order_by(FollowUp.due_at.asc())
            .limit(5)
            .all()
        )
    except Exception:
        return []
    return [
        {"what_to_recheck": f.what_to_recheck, "due_at": f.due_at} for f in rows
    ]


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)
