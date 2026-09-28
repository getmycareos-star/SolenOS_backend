"""Why two records of the same thing disagree.

The usual instinct is to trust the newer one. That is how contradictory records
become quietly wrong, because a newer record is not automatically about the same
thing: a different time of day, a different scope, a different observer's
partial view. This module checks the boring explanations first, because they are
both the most common and the most resolvable, and only treats a contradiction as
genuine once those have been ruled out.

The explicit instruction to the model is not to resolve by preferring the
recency, and the row carries a "do not resolve by" field so the tempting shortcut
stays visible to whoever reads this next.

Nothing here ever resolves a contradiction. The analysis proposes a path; only a
person can take it, and what they decided is recorded separately so the reasoning
can be re-examined later.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.care_context_enums import (
    ContradictionKind,
    ContradictionResolutionPath,
)
from app.core.llm_enums import LLMReasoningMode, LLMTask
from app.models.care import CareEvent, Evidence
from app.models.contradiction import ContradictionAnalysis, ContradictionResolution
from app.models.llm import LLMReasoningResult
from app.models.uncertainty import Contradiction as ContradictionModel
from app.services.care_common import (
    days_between,
    ensure_utc,
    evidence_map,
    parse_time,
    unique,
    utc_now,
)
from app.services.llm import LocalReasoningEngine

# Boring explanations, checked before anything else. Ordering is the argument.
_EXPLANATION_ORDER = (
    ContradictionKind.SCOPE_MISMATCH,
    ContradictionKind.SUPERSESSION,
    ContradictionKind.OBSERVER_DIFFERENCE,
    ContradictionKind.TERMINOLOGY,
    ContradictionKind.GENUINE_CONFLICT,
)

_AUTO_RESOLVABLE = {
    ContradictionKind.SCOPE_MISMATCH,
    ContradictionKind.SUPERSESSION,
    ContradictionKind.TERMINOLOGY,
}


class ContradictionEngine:
    def __init__(self, db: Session, engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self.engine = engine or LocalReasoningEngine(db)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_open(self, person_id: str) -> List[ContradictionModel]:
        return (
            self.db.query(ContradictionModel)
            .filter(
                ContradictionModel.person_id == person_id,
                ContradictionModel.resolution_status == "unresolved",
            )
            .order_by(ContradictionModel.created_at.asc())
            .all()
        )

    def get_analysis(self, contradiction_id: str) -> Optional[ContradictionAnalysis]:
        return (
            self.db.query(ContradictionAnalysis)
            .filter(
                ContradictionAnalysis.contradiction_id == contradiction_id,
                ContradictionAnalysis.is_active.is_(True),
            )
            .order_by(ContradictionAnalysis.created_at.desc())
            .first()
        )

    def list_resolutions(self, contradiction_id: str) -> List[ContradictionResolution]:
        return (
            self.db.query(ContradictionResolution)
            .filter(ContradictionResolution.contradiction_id == contradiction_id)
            .order_by(ContradictionResolution.resolved_at.desc())
            .all()
        )

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyse(
        self, contradiction_id: str, person_id: Optional[str] = None
    ) -> ContradictionAnalysis:
        """Work out why two records disagree, without pretending to settle it."""
        contradiction = (
            self.db.query(ContradictionModel)
            .filter(ContradictionModel.id == contradiction_id)
            .first()
        )
        if contradiction is None:
            raise ValueError("Contradiction not found")
        person_id = person_id or contradiction.person_id

        records = self._records_for(contradiction)
        # Records are plain dicts; evidence rows carry a single evidence_id and
        # care events carry the events own list.
        evidence_ids = unique(
            list(contradiction.evidence_ids or [])
            + [r["evidence_id"] for r in records if r.get("evidence_id")]
            + [eid for r in records for eid in (r.get("evidence_ids") or [])]
        )
        texts = evidence_map(self.db, evidence_ids)

        fallback = _deterministic_analysis(contradiction, records)
        result: Optional[LLMReasoningResult] = None
        explanations: List[Dict[str, Any]] = fallback["likely_explanations"]
        what_to_ask = fallback["what_to_ask"]
        who_can_resolve = fallback["who_can_resolve"]
        do_not_resolve_by = fallback["do_not_resolve_by"]
        summary = fallback["summary"]
        possible_context = fallback["possible_context"]
        confidence: Optional[float] = None
        result_id: Optional[str] = None

        model_result = self.engine.reason(
            task=LLMTask.CONTRADICTION_ANALYSIS,
            person_id=person_id,
            subject_type="contradiction",
            subject_id=contradiction.id,
            payload={
                "contradiction": {
                    "subject_type": contradiction.subject_type,
                    "subject_id": contradiction.subject_id,
                    "field": contradiction.field,
                    "description": contradiction.description,
                },
                "records_in_conflict": records,
                "evidence": texts,
            },
            evidence_ids=evidence_ids,
            evidence_texts=texts,
            mode=LLMReasoningMode.LLM_ASSISTED,
        )
        if model_result is not None:
            result = model_result
            detail = _detail_map(result)
            proposed = [e for e in (detail.get("likely_explanations") or []) if isinstance(e, dict)]
            if proposed:
                explanations = _rank_explanations(proposed, evidence_ids)
            what_to_ask = detail.get("what_to_ask") or what_to_ask
            who_can_resolve = detail.get("who_can_resolve") or who_can_resolve
            do_not_resolve_by = detail.get("do_not_resolve_by") or do_not_resolve_by
            summary = result.summary
            possible_context = result.possible_context or possible_context
            confidence = result.confidence
            result_id = result.id

        primary_kind = _primary_kind(explanations)
        path = _path_for(primary_kind, records)
        existing = self.get_analysis(contradiction.id)

        analysis = ContradictionAnalysis(
            person_id=person_id,
            contradiction_id=contradiction.id,
            primary_kind=primary_kind,
            likely_explanations=explanations,
            what_to_ask=what_to_ask,
            who_can_resolve=who_can_resolve,
            do_not_resolve_by=do_not_resolve_by,
            summary=summary,
            recommended_path=path,
            is_auto_resolvable=primary_kind in _AUTO_RESOLVABLE,
            evidence_ids=unique(list(result.evidence_ids or []) if result else []) or evidence_ids,
            possible_context=possible_context,
            confidence=confidence,
            reasoning_result_id=result_id,
        )
        self.db.add(analysis)
        self.db.flush()
        if existing is not None:
            existing.is_active = False
            existing.superseded_by_analysis_id = analysis.id
        self.db.flush()
        return analysis

    def _records_for(self, contradiction: ContradictionModel) -> List[Dict[str, Any]]:
        """The two or more observations that disagree, with their metadata.

        The metadata is the analysis. Who reported it, when, and from where is
        what distinguishes a genuine conflict from two people seeing different
        parts of one evening.
        """
        records: List[Dict[str, Any]] = []
        for evidence_id in contradiction.evidence_ids or []:
            evidence = (
                self.db.query(Evidence).filter(Evidence.id == evidence_id).first()
            )
            if evidence is None:
                continue
            records.append(
                {
                    "evidence_id": evidence.id,
                    "source_text": evidence.source_text,
                    "reported_by": evidence.uploaded_by_caregiver_id,
                    "uploaded_at": ensure_utc(evidence.uploaded_at).isoformat()
                    if ensure_utc(evidence.uploaded_at)
                    else None,
                    "time_provenance": evidence.time_provenance,
                    "location": evidence.location,
                    "source_type": evidence.source_type,
                    "confidence": evidence.confidence,
                }
            )

        events = (
            self.db.query(CareEvent)
            .filter(CareEvent.person_id == contradiction.person_id)
            .order_by(CareEvent.recorded_at.asc().nullslast())
            .limit(50)
            .all()
        )
        for event in events:
            if contradiction.field and contradiction.field not in (event.title or ""):
                continue
            records.append(
                {
                    "evidence_id": None,
                    "source_text": event.title,
                    "reported_by": event.created_by_caregiver_id,
                    "reporter_ids": event.reporter_ids or [],
                    "occurred_at": ensure_utc(event.occurred_at).isoformat()
                    if ensure_utc(event.occurred_at)
                    else None,
                    "recorded_at": ensure_utc(event.recorded_at).isoformat()
                    if ensure_utc(event.recorded_at)
                    else None,
                    "time_provenance": event.time_provenance,
                    "location": event.location,
                    "reporter_count": len(event.reporter_ids or []) or 1,
                    "evidence_ids": event.evidence_ids or [],
                }
            )
        return records

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def record_resolution(
        self,
        analysis_id: str,
        path_taken: Any,
        outcome: str,
        reason: Optional[str] = None,
        remaining_uncertainty: Optional[str] = None,
        caregiver_id: Optional[str] = None,
    ) -> ContradictionResolution:
        """Record what a person decided. Never applies it automatically.

        Trust rule 5: no silent updates. The decision is written to its own row
        and the underlying contradiction is only closed when a person closes it.
        """
        if not outcome or not outcome.strip():
            raise ValueError("A resolution needs an outcome")
        analysis = (
            self.db.query(ContradictionAnalysis)
            .filter(ContradictionAnalysis.id == analysis_id)
            .first()
        )
        if analysis is None:
            raise ValueError("Contradiction analysis not found")

        resolution = ContradictionResolution(
            analysis_id=analysis.id,
            person_id=analysis.person_id,
            contradiction_id=analysis.contradiction_id,
            path_taken=_enum_or_value(path_taken, ContradictionResolutionPath),
            outcome=outcome.strip(),
            reason=reason,
            remaining_uncertainty=remaining_uncertainty,
            resolved_by_caregiver_id=caregiver_id,
        )
        self.db.add(resolution)
        self.db.flush()
        return resolution

    def close_contradiction(
        self, contradiction_id: str, notes: str, caregiver_id: Optional[str] = None
    ) -> ContradictionModel:
        """Explicitly close a contradiction, with the reasoning attached."""
        contradiction = (
            self.db.query(ContradictionModel)
            .filter(ContradictionModel.id == contradiction_id)
            .first()
        )
        if contradiction is None:
            raise ValueError("Contradiction not found")
        contradiction.resolution_status = "resolved"
        contradiction.resolution_notes = notes
        contradiction.resolved_at = utc_now()
        contradiction.resolved_by_caregiver_id = caregiver_id
        self.db.flush()
        return contradiction

    def unresolved_summary(self, person_id: str) -> Dict[str, Any]:
        """Every open contradiction with what is known about resolving it."""
        rows = []
        for contradiction in self.list_open(person_id):
            analysis = self.get_analysis(contradiction.id)
            rows.append(
                {
                    "contradiction": contradiction,
                    "analysis": analysis,
                    "resolutions": self.list_resolutions(contradiction.id),
                    "can_be_closed": analysis is not None and analysis.is_auto_resolvable,
                }
            )
        unanalysed = [r for r in rows if r["analysis"] is None]
        return {
            "person_id": person_id,
            "open_count": len(rows),
            "unanalysed_count": len(unanalysed),
            "items": rows,
            "note": (
                "None of these are resolved automatically. A contradiction is closed when a person "
                "closes it, and the reasoning for that is kept."
            ),
        }


def _deterministic_analysis(
    contradiction: ContradictionModel, records: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """Structural analysis with no model involved.

    The scope check is the important one: if the records carry different times
    or different locations, they may not be about the same occurrence at all, and
    that beats every other explanation.
    """
    explanations: List[Dict[str, Any]] = []

    times = {r.get("occurred_at") for r in records if r.get("occurred_at")}
    locations = {r.get("location") for r in records if r.get("location")}
    reporters = {r.get("reported_by") for r in records if r.get("reported_by")}

    if len(times) > 1:
        explanations.append(
            {
                "explanation": (
                    f"The records are about {len(times)} different times. They may not describe the same "
                    "occurrence."
                ),
                "kind": ContradictionKind.SCOPE_MISMATCH.value,
                "how_to_test": "Check whether each record is about a different occasion or time of day.",
                "supporting_evidence_ids": [],
            }
        )
    if len(locations) > 1:
        explanations.append(
            {
                "explanation": f"The records come from {len(locations)} different places.",
                "kind": ContradictionKind.SCOPE_MISMATCH.value,
                "how_to_test": "Establish whether the same behaviour or state occurs in more than one setting.",
                "supporting_evidence_ids": [],
            }
        )
    if len(reporters) > 1:
        explanations.append(
            {
                "explanation": (
                    f"{len(reporters)} different people reported these. They may have seen different parts "
                    "of the same period."
                ),
                "kind": ContradictionKind.OBSERVER_DIFFERENCE.value,
                "how_to_test": "Ask each reporter what they personally observed, not what they were told.",
                "supporting_evidence_ids": [],
            }
        )
    if len(records) >= 2 and times:
        ordered = sorted(t for t in times)
        gap = days_between(parse_time(ordered[0]), parse_time(ordered[-1]))
        if gap is not None and gap > 0:
            explanations.append(
                {
                    "explanation": (
                        f"The records are {gap} day(s) apart. The situation may simply have changed in "
                        "between."
                    ),
                    "kind": ContradictionKind.SUPERSESSION.value,
                    "how_to_test": (
                        "Establish which describes the state now. Only then is the older record a "
                        "superseded one rather than a conflicting one."
                    ),
                    "supporting_evidence_ids": [],
                }
            )
    if not explanations:
        explanations.append(
            {
                "explanation": (
                    "Nothing in the metadata rules out a genuine disagreement between what two people "
                    "saw."
                ),
                "kind": ContradictionKind.GENUINE_CONFLICT.value,
                "how_to_test": "Put the two accounts to the people who were there, separately.",
                "supporting_evidence_ids": [],
            }
        )

    explanations = _rank_explanations(explanations, [])

    return {
        "likely_explanations": explanations,
        "what_to_ask": (
            "Ask each reporter what they personally observed, and when. Do not ask whether they are "
            "correct, which invites defensiveness rather than information."
        ),
        "who_can_resolve": "caregiver_present_at_the_time",
        "do_not_resolve_by": (
            "Do not resolve this by taking the more recent record. Newer is not the same as about the "
            "same thing, and preferring recency silently discards a real disagreement."
        ),
        "summary": (
            f"{len(explanations)} possible explanation(s) for a disagreement about "
            f"{contradiction.field or 'this'}."
        ),
        "possible_context": (
            "Without knowing the times and places each record refers to, most of these explanations "
            "cannot be distinguished from one another."
        ),
    }


def _rank_explanations(
    explanations: Sequence[Dict[str, Any]], permitted: Sequence[str]
) -> List[Dict[str, Any]]:
    """Put the resolvable explanations first.

    Order is the argument: a scope mismatch is checkable today, a genuine
    conflict is not, so the checkable one is what to go and do.
    """
    allowed = set(permitted or [])
    cleaned: List[Dict[str, Any]] = []
    for explanation in explanations:
        if not isinstance(explanation, dict):
            continue
        text = str(explanation.get("explanation") or "").strip()
        if not text:
            continue
        supporting = [
            eid
            for eid in (explanation.get("supporting_evidence_ids") or [])
            if isinstance(eid, str) and eid in allowed
        ]
        cleaned.append(
            {
                "explanation": text[:500],
                "kind": _kind_value(explanation.get("kind")),
                "how_to_test": (str(explanation.get("how_to_test") or "").strip() or None),
                "supporting_evidence_ids": unique(supporting),
            }
        )

    cleaned.sort(key=lambda item: _kind_rank(item["kind"]))
    return cleaned


def _kind_value(value: Any) -> str:
    if value is None:
        return ContradictionKind.OTHER.value
    raw = value.value if hasattr(value, "value") else str(value)
    try:
        return ContradictionKind(raw).value
    except ValueError:
        return ContradictionKind.OTHER.value


def _kind_rank(kind: str) -> int:
    try:
        return _EXPLANATION_ORDER.index(ContradictionKind(kind))
    except ValueError:
        return len(_EXPLANATION_ORDER)


def _primary_kind(explanations: Sequence[Dict[str, Any]]) -> Optional[ContradictionKind]:
    if not explanations:
        return None
    try:
        return ContradictionKind(explanations[0]["kind"])
    except (ValueError, KeyError):
        return ContradictionKind.OTHER


def _path_for(
    kind: Optional[ContradictionKind], records: Sequence[Dict[str, Any]]
) -> ContradictionResolutionPath:
    if kind == ContradictionKind.SCOPE_MISMATCH:
        return ContradictionResolutionPath.ACCEPT_BOTH
    if kind == ContradictionKind.SUPERSESSION:
        return ContradictionResolutionPath.OBSERVE_FURTHER
    if kind == ContradictionKind.TERMINOLOGY:
        return ContradictionResolutionPath.ASK_OBSERVER
    if kind == ContradictionKind.OBSERVER_DIFFERENCE:
        return ContradictionResolutionPath.ASK_OBSERVER
    if kind == ContradictionKind.GENUINE_CONFLICT:
        reporters = {r.get("reported_by") for r in records if r.get("reported_by")}
        return (
            ContradictionResolutionPath.ASK_CLINICIAN
            if len(reporters) > 1
            else ContradictionResolutionPath.ASK_OBSERVER
        )
    return ContradictionResolutionPath.CAREGIVER_DECIDES


def _detail_map(result: LLMReasoningResult) -> Dict[str, Any]:
    detail: Dict[str, Any] = {}
    for item in result.detail or []:
        if isinstance(item, dict) and "field" in item:
            detail[item["field"]] = item.get("value")
    return detail


def _enum_or_value(value: Any, enum_cls) -> Any:
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    raw = value.value if hasattr(value, "value") else str(value)
    try:
        return enum_cls(raw)
    except ValueError:
        fallback = getattr(enum_cls, "OTHER", None) or list(enum_cls)[0]
        return fallback
