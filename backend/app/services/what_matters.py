"""What matters.

This is the layer that decides whether an action is worth doing at all, as
distinct from whether it is urgent. The repo already has a prioritization
engine, and it is good, and it answers "what should I do first". This answers a
different question: "is this the right thing to be doing for this person".

The distinction is kept structural rather than rhetorical. These entries are
keyed on values, goals, preferences, identity and dignity. They are not keyed on
severity, and nothing here is derived from a prioritisation score. Keeping them
apart matters because they genuinely conflict: a task can be clinically urgent
and completely beside the point, and a low-stakes routine can matter enormously
to someone whose days have narrowed to very little.

Where the person cannot speak for themselves, the record carries what they
chose, what they complained about, what they looked forward to, and what the
family says they treasure. Absence of that is reported as absence.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.care_context_enums import AlignmentVerdict, MattersCategory, MattersImportance
from app.core.llm_enums import LLMReasoningMode, LLMTask
from app.models.care import CareEvent
from app.models.llm import LLMReasoningResult
from app.models.what_matters import (
    AlignmentAssessment,
    MattersTension,
    WhatMattersEntry,
)
from app.services.care_common import (
    evidence_map,
    unique,
)
from app.services.llm import LocalReasoningEngine

_HISTORY_LIMIT = 40

# Signals that an entry came from the person rather than from someone else
# speaking about them. A stated preference and a family's inference are not the
# same kind of evidence and the difference is recorded rather than flattened.
_PERSON_VOICE_MARKERS = (
    "she said",
    "he said",
    "they said",
    "says she",
    "says he",
    "always said",
    "used to say",
    "told me",
    "asked for",
    "wants",
    "prefers",
    "loves",
    "hates",
    "enjoys",
    "her own",
    "his own",
    "used to",
    "always has",
    "never liked",
)


class WhatMattersEngine:
    def __init__(self, db: Session, engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self.engine = engine or LocalReasoningEngine(db)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_entries(
        self,
        person_id: str,
        category: Optional[Any] = None,
        minimum_importance: Optional[Any] = None,
    ) -> List[WhatMattersEntry]:
        query = self.db.query(WhatMattersEntry).filter(
            WhatMattersEntry.person_id == person_id, WhatMattersEntry.is_active.is_(True)
        )
        if category is not None:
            query = query.filter(WhatMattersEntry.category == _enum_or_value(category, MattersCategory))
        entries = query.order_by(WhatMattersEntry.created_at.desc()).all()
        if minimum_importance is not None:
            floor = _importance_rank(_enum_or_value(minimum_importance, MattersImportance))
            entries = [e for e in entries if _importance_rank(e.importance) >= floor]
        return entries

    def list_tensions(self, person_id: str) -> List[MattersTension]:
        return (
            self.db.query(MattersTension)
            .filter(
                MattersTension.person_id == person_id,
                MattersTension.is_active.is_(True),
            )
            .all()
        )

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def add_entry(
        self,
        person_id: str,
        statement: str,
        category: Any = MattersCategory.VALUE,
        importance: Any = MattersImportance.MODERATE,
        who_it_matters_to: Optional[str] = None,
        how_we_know: Optional[str] = None,
        what_it_rules_out: Optional[Sequence[str]] = None,
        source_quote: Optional[str] = None,
        stated_by_caregiver_id: Optional[str] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        confidence: Optional[float] = None,
        possible_context: Optional[str] = None,
        flush: bool = True,
    ) -> WhatMattersEntry:
        if not statement or not statement.strip():
            raise ValueError("An entry needs a statement")
        entry = WhatMattersEntry(
            person_id=person_id,
            statement=statement.strip(),
            category=_enum_or_value(category, MattersCategory),
            importance=_enum_or_value(importance, MattersImportance),
            who_it_matters_to=who_it_matters_to,
            how_we_know=how_we_know,
            what_it_rules_out=list(what_it_rules_out) if what_it_rules_out else None,
            source_quote=source_quote,
            stated_by_caregiver_id=stated_by_caregiver_id,
            attributed_to_person=_attributed_to_person(statement, source_quote),
            evidence_ids=list(evidence_ids) if evidence_ids else None,
            confidence=confidence,
            possible_context=possible_context,
        )
        self.db.add(entry)
        if flush:
            self.db.flush()
        return entry

    def add_tension(
        self,
        person_id: str,
        between: str,
        note: Optional[str] = None,
        entry_ids: Optional[Sequence[str]] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        confidence: Optional[float] = None,
        flush: bool = True,
    ) -> MattersTension:
        tension = MattersTension(
            person_id=person_id,
            between=between,
            note=note,
            entry_ids=list(entry_ids) if entry_ids else None,
            evidence_ids=list(evidence_ids) if evidence_ids else None,
            confidence=confidence,
        )
        self.db.add(tension)
        if flush:
            self.db.flush()
        return tension

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract(self, person_id: str, limit: int = _HISTORY_LIMIT) -> List[WhatMattersEntry]:
        """Look for what this person values in what has been recorded.

        The model proposes; every proposed entry is a candidate until it is
        stored. Entries that restate a clinical fact rather than a value are
        dropped by the category check below, because "has difficulty walking" is
        not a thing someone cares about - it is a thing that is true about them.
        """
        events = (
            self.db.query(CareEvent)
            .filter(CareEvent.person_id == person_id)
            .order_by(CareEvent.recorded_at.desc().nullslast())
            .limit(limit)
            .all()
        )
        if not events:
            return []

        evidence_ids = unique([eid for event in events for eid in (event.evidence_ids or [])])
        texts = evidence_map(self.db, evidence_ids)
        existing = {(e.statement or "").strip().lower() for e in self.list_entries(person_id)}

        result = self.engine.reason(
            task=LLMTask.WHAT_MATTERS,
            person_id=person_id,
            subject_type="what_matters",
            payload={
                "recorded_events": [
                    {
                        "title": event.title,
                        "description": event.description,
                        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                        "evidence_ids": event.evidence_ids or [],
                    }
                    for event in events
                ],
                "already_recorded": [e.statement for e in self.list_entries(person_id)],
                "evidence": texts,
            },
            evidence_ids=evidence_ids,
            evidence_texts=texts,
            mode=LLMReasoningMode.LLM_DRAFT,
        )
        if result is None:
            return []

        created: List[WhatMattersEntry] = []
        for entry in _detail_map(result).get("entries") or []:
            if not isinstance(entry, dict):
                continue
            statement = str(entry.get("statement") or "").strip()
            if not statement or statement.lower() in existing:
                continue
            if _looks_like_clinical_fact(statement):
                # True about them, not valued by them. Belongs in the record
                # already; recording it here would conflate the two axes.
                continue
            record = self.add_entry(
                person_id=person_id,
                statement=statement,
                category=_enum_or_value(entry.get("category"), MattersCategory),
                importance=_enum_or_value(entry.get("how_much_it_matters"), MattersImportance),
                who_it_matters_to=entry.get("who_it_matters_to"),
                how_we_know=entry.get("how_we_know"),
                what_it_rules_out=entry.get("what_it_rules_out")
                if isinstance(entry.get("what_it_rules_out"), list)
                else None,
                evidence_ids=result.evidence_ids or None,
                confidence=result.confidence,
                possible_context=result.possible_context,
                flush=False,
            )
            existing.add(statement.lower())
            created.append(record)

        for tension in _detail_map(result).get("tensions") or []:
            if not isinstance(tension, dict) or not tension.get("between"):
                continue
            self.add_tension(
                person_id=person_id,
                between=str(tension["between"]),
                note=tension.get("note"),
                evidence_ids=result.evidence_ids or None,
                confidence=result.confidence,
                flush=False,
            )

        self.db.flush()
        return created

    # ------------------------------------------------------------------
    # Alignment
    # ------------------------------------------------------------------

    def assess_action(
        self,
        person_id: str,
        action_description: str,
        action_ref_type: Optional[str] = None,
        action_ref_id: Optional[str] = None,
    ) -> AlignmentAssessment:
        """Does this action serve what this person actually cares about?

        Reported separately from safety and from priority. An action can be safe,
        urgent, and still not serve this person, and that is worth saying out
        loud rather than burying in a prioritisation score.
        """
        if not action_description or not action_description.strip():
            raise ValueError("An action description is required")

        entries = self.list_entries(person_id)
        tensions = self.list_tensions(person_id)
        evidence_ids = unique([eid for entry in entries for eid in (entry.evidence_ids or [])])

        fallback = _deterministic_alignment(action_description, entries)
        if not entries:
            assessment = AlignmentAssessment(
                person_id=person_id,
                action_description=action_description.strip(),
                action_ref_type=action_ref_type,
                action_ref_id=action_ref_id,
                verdict=AlignmentVerdict.UNKNOWN,
                reasoning=fallback["reasoning"],
                possible_context=(
                    "Nothing has been recorded about what this person values, so it is not possible to say "
                    "whether this serves them. That is the gap, not a verdict."
                ),
                confidence=None,
                evidence_ids=evidence_ids,
            )
            self.db.add(assessment)
            self.db.flush()
            return assessment

        result = self.engine.reason(
            task=LLMTask.WHAT_MATTERS_ALIGNMENT,
            person_id=person_id,
            subject_type="alignment",
            subject_id=action_ref_id,
            payload={
                "proposed_action": action_description,
                "what_matters": [
                    {
                        "statement": e.statement,
                        "category": _enum_value(e.category),
                        "who_it_matters_to": e.who_it_matters_to,
                        "importance": _enum_value(e.importance),
                        "what_it_rules_out": e.what_it_rules_out or [],
                    }
                    for e in entries
                ],
                "known_tensions": [{"between": t.between, "note": t.note} for t in tensions],
                "evidence": evidence_map(self.db, evidence_ids),
            },
            evidence_ids=evidence_ids,
            evidence_texts=evidence_map(self.db, evidence_ids),
            mode=LLMReasoningMode.LLM_ASSISTED,
        )

        verdict = fallback["verdict"]
        reasoning = fallback["reasoning"]
        tension_note: Optional[str] = None
        adaptation: Optional[str] = None
        possible_context = "Whether this reflects the person's current wishes is not established; it is what the record currently says."
        confidence: Optional[float] = None
        used_ids = evidence_ids

        if result is not None:
            detail = _detail_map(result)
            verdict = _enum_or_value(detail.get("verdict"), AlignmentVerdict)
            reasoning = result.summary
            tension_note = detail.get("tension")
            adaptation = detail.get("suggested_adaptation")
            possible_context = result.possible_context or possible_context
            confidence = result.confidence
            used_ids = unique(list(result.evidence_ids or []) + evidence_ids)

        assessment = AlignmentAssessment(
            person_id=person_id,
            action_description=action_description.strip(),
            action_ref_type=action_ref_type,
            action_ref_id=action_ref_id,
            verdict=verdict,
            reasoning=reasoning,
            tension=tension_note,
            suggested_adaptation=adaptation,
            entry_ids=[e.id for e in entries],
            evidence_ids=used_ids,
            possible_context=possible_context,
            confidence=confidence,
            reasoning_result_id=result.id if result is not None else None,
        )
        self.db.add(assessment)
        self.db.flush()
        return assessment

    def what_matters_view(self, person_id: str) -> Dict[str, Any]:
        """The whole layer in one payload, including its own gaps."""
        entries = self.list_entries(person_id)
        tensions = self.list_tensions(person_id)
        by_category: Dict[str, int] = {}
        for entry in entries:
            key = _enum_value(entry.category)
            by_category[key] = by_category.get(key, 0) + 1

        return {
            "person_id": person_id,
            "entry_count": len(entries),
            "by_importance": {
                _enum_value(level): len(
                    [e for e in entries if _enum_value(e.importance) == _enum_value(level)]
                )
                for level in MattersImportance
            },
            "by_category": by_category,
            "entries": [
                {
                    "id": e.id,
                    "statement": e.statement,
                    "category": _enum_value(e.category),
                    "importance": _enum_value(e.importance),
                    "who_it_matters_to": e.who_it_matters_to,
                    "attributed_to_person": e.attributed_to_person,
                    "what_it_rules_out": e.what_it_rules_out or [],
                    "evidence_ids": e.evidence_ids or [],
                    "confidence": e.confidence,
                    "possible_context": e.possible_context,
                }
                for e in entries
            ],
            "tensions": [
                {"between": t.between, "note": t.note, "entry_ids": t.entry_ids or []}
                for t in tensions
            ],
            "known_gaps": _gaps(entries, tensions),
        }


_CLINICAL_FACT_MARKERS = (
    "has difficulty",
    "is unsteady",
    "needs help",
    "cannot walk",
    "is unable",
    "blood pressure",
    "takes",
    "medication",
    "prescribed",
    "diagnosed",
    "assessment",
    "score of",
    "mobility is",
    "continence",
)


def _importance_rank(value: Any) -> int:
    order = {
        MattersImportance.LOW: 0,
        MattersImportance.MODERATE: 1,
        MattersImportance.HIGH: 2,
        MattersImportance.DEFINING: 3,
    }
    for member, rank in order.items():
        if value == member or _enum_value(value) == member.value:
            return rank
    return 0


def _attributed_to_person(statement: str, source_quote: Optional[str]) -> bool:
    """Was this said by the person, or said about them?

    Worth knowing separately. A preference the person voiced is stronger ground
    for letting it guide a decision than a relative's inference, and the two
    should not be recorded identically.
    """
    text = f"{statement or ''} {source_quote or ''}".lower()
    return any(marker in text for marker in _PERSON_VOICE_MARKERS)


def _looks_like_clinical_fact(statement: str) -> bool:
    lowered = (statement or "").lower()
    return any(marker in lowered for marker in _CLINICAL_FACT_MARKERS)


def _deterministic_alignment(
    action_description: str, entries: Sequence[WhatMattersEntry]
) -> Dict[str, Any]:
    """Keyword overlap fallback.

    Weak by construction, and honest about it. It exists so that an alignment
    verdict is available with no model running, not because keyword matching can
    decide whether something serves a person.
    """
    if not entries:
        return {
            "verdict": AlignmentVerdict.UNKNOWN,
            "reasoning": "Nothing is recorded about what this person values.",
        }

    action_words = {word for word in _tokens(action_description) if len(word) > 3}
    scored: List[tuple] = []
    for entry in entries:
        entry_words = {word for word in _tokens(entry.statement) if len(word) > 3}
        if not action_words or not entry_words:
            continue
        overlap = len(action_words & entry_words) / len(action_words)
        scored.append((overlap, entry))
    scored.sort(key=lambda item: item[0], reverse=True)

    if scored and scored[0][0] >= 0.34:
        best = scored[0][1]
        return {
            "verdict": AlignmentVerdict.PARTLY_SERVES,
            "reasoning": (
                f"Shares wording with something recorded as mattering to this person: '{best.statement}'. "
                "This is a keyword match, not a judgement about whether the action actually serves it."
            ),
        }
    return {
        "verdict": AlignmentVerdict.UNKNOWN,
        "reasoning": (
            "Nothing recorded about what this person values overlaps with the wording of this action. "
            "That is not evidence the action is wrong, only that the record cannot answer the question."
        ),
    }


def _tokens(text: str) -> List[str]:
    cleaned = "".join(ch if ch.isalnum() else " " for ch in (text or "").lower())
    return cleaned.split()


def _gaps(entries: Sequence[WhatMattersEntry], tensions: Sequence[MattersTension]) -> List[str]:
    gaps: List[str] = []
    if not entries:
        gaps.append(
            "Nothing is recorded about what this person values, so no action can be checked against it."
        )
        return gaps
    if not any(e.attributed_to_person for e in entries):
        gaps.append(
            "Every entry is someone else's account. The person's own preferences are not in the record."
        )
    if not any(
        _importance_rank(e.importance) >= _importance_rank(MattersImportance.HIGH) for e in entries
    ):
        gaps.append("Nothing has been recorded as mattering a great deal to this person.")
    if not tensions:
        gaps.append(
            "No tensions between competing values have been recorded, which is unlikely to be true."
        )
    return gaps


def _detail_map(result: LLMReasoningResult) -> Dict[str, Any]:
    detail: Dict[str, Any] = {}
    for item in result.detail or []:
        if isinstance(item, dict) and "field" in item:
            detail[item["field"]] = item.get("value")
    return detail


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _enum_or_value(value: Any, enum_cls) -> Any:
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    raw = _enum_value(value)
    try:
        return enum_cls(raw)
    except ValueError:
        fallback = getattr(enum_cls, "UNKNOWN", None) or getattr(enum_cls, "OTHER", None)
        return fallback if fallback is not None else value
