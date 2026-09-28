"""The individual as the centre of the record.

Every other layer in this module set asks "what does the record say about the
situation". This one asks "who is this person", and it is the layer that makes
the difference between a care system that knows about dementia and one that
knows about this person.

Two rules shape the design:

Attributes are held separately from interpretations and from changes. What
someone likes, what settles them, and who matters to them does not change week to
week, so it is recorded once and trusted. A decline is not an attribute; it is a
trajectory, and it is tracked elsewhere.

A thin profile is a legitimate, reported state. The record for a person admitted
three days ago is genuinely almost empty, and the honest output is "we do not
know this person yet, here is what to go and ask", not a plausible paragraph
about people in general.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.care_context_enums import (
    PersonAttributeDomain,
    ProfileCompleteness,
)
from app.core.llm_enums import LLMReasoningMode, LLMTask
from app.models.llm import LLMReasoningResult
from app.models.person_context import (
    PersonAttribute,
    PersonCalmer,
    PersonProfile,
    PersonTrigger,
)
from app.services.care_common import (
    evidence_map,
    unique,
)
from app.services.llm import LocalReasoningEngine

_THIN_PROFILE = 3
_PARTIAL_PROFILE = 8


class PersonModelEngine:
    """Builds and maintains the picture of a specific person."""

    def __init__(self, db: Session, engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self.engine = engine or LocalReasoningEngine(db)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def get_profile(self, person_id: str) -> Optional[PersonProfile]:
        return (
            self.db.query(PersonProfile)
            .filter(PersonProfile.person_id == person_id, PersonProfile.is_active.is_(True))
            .order_by(PersonProfile.created_at.desc())
            .first()
        )

    def list_attributes(
        self, person_id: str, domain: Optional[str] = None
    ) -> List[PersonAttribute]:
        query = self.db.query(PersonAttribute).filter(
            PersonAttribute.person_id == person_id, PersonAttribute.is_active.is_(True)
        )
        if domain:
            query = query.filter(PersonAttribute.domain == domain)
        return query.order_by(PersonAttribute.created_at.desc()).all()

    def list_triggers(self, person_id: str) -> List[PersonTrigger]:
        return (
            self.db.query(PersonTrigger)
            .filter(PersonTrigger.person_id == person_id, PersonTrigger.is_active.is_(True))
            .order_by(PersonTrigger.occurrences.desc().nullslast(), PersonTrigger.created_at.desc())
            .all()
        )

    def list_calmers(self, person_id: str) -> List[PersonCalmer]:
        return (
            self.db.query(PersonCalmer)
            .filter(PersonCalmer.person_id == person_id, PersonCalmer.is_active.is_(True))
            .all()
        )

    def trigger_view(self, person_id: str) -> List[Dict[str, Any]]:
        """Triggers in the shape the reasoning prompts expect."""
        return [
            {
                "trigger": row.trigger,
                "response_seen": row.response_seen,
                "occurrences": row.occurrences,
                "calming_measure": row.calming_measure,
            }
            for row in self.list_triggers(person_id)
        ]

    def calmer_view(self, person_id: str) -> List[Dict[str, Any]]:
        return [{"measure": row.measure, "context": row.context} for row in self.list_calmers(person_id)]

    def describe(self, person_id: str) -> Dict[str, Any]:
        """A caregiver-readable summary, safe to show without further gating."""
        profile = self.get_profile(person_id)
        attributes = self.list_attributes(person_id)
        triggers = self.list_triggers(person_id)
        calmers = self.list_calmers(person_id)

        return {
            "person_id": person_id,
            "profile": profile,
            "completeness": profile.completeness if profile else ProfileCompleteness.THIN,
            "attribute_count": len(attributes),
            "trigger_count": len(triggers),
            "calmer_count": len(calmers),
            "what_settles_them": [c.measure for c in calmers],
            "what_upsets_them": [t.trigger for t in triggers],
            "attributes": [
                {
                    "id": a.id,
                    "domain": a.domain.value if hasattr(a.domain, "value") else a.domain,
                    "label": a.label,
                    "value": a.value,
                    "confidence": a.confidence,
                    "possible_context": a.possible_context,
                }
                for a in attributes
            ],
            "known_about_them": (
                "The record establishes something specific about this person."
                if attributes or calmers
                else "The record does not yet establish much about this person. "
                "Asking about their history, preferences and what settles them is "
                "the most useful thing to do first."
            ),
        }

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def add_attribute(
        self,
        person_id: str,
        domain: Any,
        label: str,
        value: str,
        evidence_ids: Optional[Sequence[str]] = None,
        source_quote: Optional[str] = None,
        stated_by_caregiver_id: Optional[str] = None,
        confidence: Optional[float] = None,
        possible_context: Optional[str] = None,
        flush: bool = True,
    ) -> PersonAttribute:
        """Record one thing about this person, written by a person.

        Confidence stays None unless the caller genuinely has a basis for it. A
        caregiver's direct statement is evidence, not a number, and inventing a
        number here would be the same fabrication problem in miniature.
        """
        attribute = PersonAttribute(
            person_id=person_id,
            domain=_enum_or_value(domain, PersonAttributeDomain),
            label=label,
            value=value,
            evidence_ids=list(evidence_ids) if evidence_ids else None,
            source_quote=source_quote,
            stated_by_caregiver_id=stated_by_caregiver_id,
            confidence=confidence,
            possible_context=possible_context,
        )
        self.db.add(attribute)
        if flush:
            self.db.flush()
        return attribute

    def add_trigger(
        self,
        person_id: str,
        trigger: str,
        response_seen: Optional[str] = None,
        occurrences: Optional[int] = None,
        calming_measure: Optional[str] = None,
        category: Optional[str] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        last_observed_at: Optional[Any] = None,
        confidence: Optional[float] = None,
        possible_context: Optional[str] = None,
        flush: bool = True,
    ) -> PersonTrigger:
        if not trigger or not str(trigger).strip():
            raise ValueError("A trigger must be named concretely")
        record = PersonTrigger(
            person_id=person_id,
            trigger=str(trigger).strip(),
            response_seen=response_seen,
            occurrences=occurrences,
            calming_measure=calming_measure,
            category=category,
            evidence_ids=list(evidence_ids) if evidence_ids else None,
            last_observed_at=last_observed_at,
            confidence=confidence,
            possible_context=possible_context,
        )
        self.db.add(record)
        if flush:
            self.db.flush()
        return record

    def add_calmer(
        self,
        person_id: str,
        measure: str,
        context: Optional[str] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        confidence: Optional[float] = None,
        flush: bool = True,
    ) -> PersonCalmer:
        if not measure or not str(measure).strip():
            raise ValueError("A calming measure must be named concretely")
        record = PersonCalmer(
            person_id=person_id,
            measure=str(measure).strip(),
            context=context,
            evidence_ids=list(evidence_ids) if evidence_ids else None,
            confidence=confidence,
        )
        self.db.add(record)
        if flush:
            self.db.flush()
        return record

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    def build_profile(self, person_id: str, as_of: Optional[Any] = None) -> PersonProfile:
        """Rebuild the person profile from the record and, if it can, the model.

        The deterministic part always runs and always produces a usable profile.
        The model only ever enriches the narrative fields; when it is unavailable
        or the guard rejects its output, the profile is still built and its
        summary says so.
        """
        attributes = self.list_attributes(person_id)
        triggers = self.list_triggers(person_id)
        calmers = self.list_calmers(person_id)

        evidence_ids = unique(
            list(attributes) and [e for a in attributes for e in (a.evidence_ids or [])]
            + [e for t in triggers for e in (t.evidence_ids or [])]
            + [e for c in calmers for e in (c.evidence_ids or [])]
        )
        completeness = _completeness(len(attributes), len(calmers))

        narrative = self._narrate(person_id, attributes, triggers, calmers, evidence_ids, completeness)

        existing = self.get_profile(person_id)
        profile = PersonProfile(
            person_id=person_id,
            completeness=completeness,
            attribute_count=len(attributes),
            trigger_count=len(triggers),
            calmer_count=len(calmers),
            summary=narrative["summary"],
            personalisation=narrative.get("personalisation"),
            communication_style=narrative.get("communication_style"),
            what_settles_them=[c.measure for c in calmers],
            what_upsets_them=[t.trigger for t in triggers],
            important_people=narrative.get("important_people"),
            evidence_ids=evidence_ids,
            possible_context=narrative.get("possible_context"),
            confidence=narrative.get("confidence"),
            reasoning_result_id=(narrative.get("result") or None)
            and narrative["result"].id,
        )
        self.db.add(profile)
        self.db.flush()
        self._ingest_model_output(profile, narrative)

        if existing is not None:
            existing.is_active = False
            existing.superseded_by_profile_id = profile.id
        self.db.flush()
        return profile

    def _narrate(
        self,
        person_id: str,
        attributes: Sequence[PersonAttribute],
        triggers: Sequence[PersonTrigger],
        calmers: Sequence[PersonCalmer],
        evidence_ids: Sequence[str],
        completeness: ProfileCompleteness,
    ) -> Dict[str, Any]:
        """Deterministic narrative first, model enrichment second."""
        fallback = _deterministic_narrative(attributes, triggers, calmers, completeness)

        result: Optional[LLMReasoningResult] = self.engine.reason(
            task=LLMTask.PERSON_PROFILE,
            person_id=person_id,
            subject_type="person",
            subject_id=person_id,
            payload={
                "recorded_attributes": [
                    {
                        "domain": _enum_value(a.domain),
                        "label": a.label,
                        "value": a.value,
                        "quote": a.source_quote,
                    }
                    for a in attributes
                ],
                "recorded_triggers": [
                    {
                        "trigger": t.trigger,
                        "response_seen": t.response_seen,
                        "occurrences": t.occurrences,
                        "calming_measure": t.calming_measure,
                    }
                    for t in triggers
                ],
                "recorded_calming_measures": [{"measure": c.measure, "context": c.context} for c in calmers],
                "evidence": evidence_map(self.db, evidence_ids),
                "completeness": completeness.value,
            },
            evidence_ids=evidence_ids,
            evidence_texts=evidence_map(self.db, evidence_ids),
            mode=LLMReasoningMode.LLM_ASSISTED,
        )

        if result is None:
            return fallback

        detail = _detail_map(result)
        important_people = detail.get("important_people")
        return {
            "summary": result.summary,
            "personalisation": detail.get("personalisation") or fallback.get("personalisation"),
            "communication_style": detail.get("communication_style"),
            "important_people": important_people if isinstance(important_people, list) else None,
            "possible_context": result.possible_context or fallback.get("possible_context"),
            "confidence": result.confidence,
            "result": result,
        }

    def _ingest_model_output(self, profile: PersonProfile, narrative: Dict[str, Any]) -> None:
        """Fold any model-proposed attributes into the person model.

        The guard has already stripped unsupported citations and withheld
        confidence that had no basis. What arrives here is a proposal about this
        person that the record can support, so it is worth persisting as an
        attribute rather than throwing away.
        """
        result = narrative.get("result")
        if result is None:
            return
        for entry in _detail_map(result).get("attributes") or []:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label")
            value = entry.get("value")
            if not label or not value:
                continue
            domain = entry.get("domain")
            self.add_attribute(
                person_id=profile.person_id,
                domain=_enum_or_value(domain, PersonAttributeDomain),
                label=str(label)[:120],
                value=str(value),
                evidence_ids=result.evidence_ids or None,
                confidence=result.confidence,
                possible_context=result.possible_context,
                flush=False,
            )


def _deterministic_narrative(
    attributes: Sequence[PersonAttribute],
    triggers: Sequence[PersonTrigger],
    calmers: Sequence[PersonCalmer],
    completeness: ProfileCompleteness,
) -> Dict[str, Any]:
    """The profile as it stands from recorded attributes alone.

    This is not a placeholder. It is the honest answer when there is no model,
    and for a well-recorded person it is a perfectly good one.
    """
    if completeness == ProfileCompleteness.THIN:
        return {
            "summary": (
                "The record does not yet say much about this person. That is common early on and it is "
                "the first thing worth fixing, because almost every other part of the record gets easier "
                "once we know who we are looking after."
            ),
            "personalisation": None,
            "possible_context": (
                "We do not yet know their history, what they enjoy, what settles them, or who matters to "
                "them. None of that is in the record to answer from."
            ),
            "confidence": None,
        }

    parts = [f"The record establishes {len(attributes)} specific things about this person."]
    domains: Dict[str, int] = {}
    for attribute in attributes:
        key = _enum_value(attribute.domain)
        domains[key] = domains.get(key, 0) + 1
    parts.append("Most of it sits in " + _join_sorted(domains) + ".")

    if calmers:
        parts.append(
            "What settles them: " + "; ".join(c.measure for c in calmers[:5]) + "."
        )
    if triggers:
        parts.append(
            "What sets them off: " + "; ".join(t.trigger for t in triggers[:5]) + "."
        )

    return {
        "summary": " ".join(parts),
        "personalisation": None,
        "possible_context": (
            "The record says who this person is, not how they have changed over time. Nothing here should "
            "be read as a current picture without checking when it was last confirmed."
        ),
        "confidence": None,
    }


def _completeness(attribute_count: int, calmer_count: int) -> ProfileCompleteness:
    total = attribute_count + calmer_count
    if total >= _PARTIAL_PROFILE:
        return ProfileCompleteness.SUBSTANTIAL
    if total >= _THIN_PROFILE:
        return ProfileCompleteness.PARTIAL
    return ProfileCompleteness.THIN


def _join_sorted(counts: Dict[str, int]) -> str:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{key} ({value})" for key, value in ordered)


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
        return enum_cls.OTHER if hasattr(enum_cls, "OTHER") else value
