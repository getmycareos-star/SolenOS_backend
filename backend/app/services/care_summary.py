"""The Living Care Summary.

A clinician has about ten minutes in an appointment and will ask three things:
what has changed, what are you worried about, and what would you like to change.
This module assembles the record to answer those, and stops there. It does not
diagnose, does not recommend a dose, and does not hide the parts nobody knows -
because a summary that reads confidently is the one thing that will make a
clinician trust it wrongly.

Every claim in the output keeps its evidence ids. A finding that cannot be
traced to something a caregiver actually observed is reported as unestablished
rather than dropped, so a gap is visible instead of invisible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.core.sharing_enums import ShareCategory
from app.models.identity import Person
from app.models.care_event import CareEvent
from app.models.caregiver import Caregiver
from app.models.contradiction import ContradictionAnalysis
from app.models.daily_capture import MedicationRecord, ScaleObservation, VitalSign
from app.models.follow_up import FollowUp
from app.models.meaning import Meaning
from app.services.care_sharing import CareSharingService
from app.services.daily_capture import DailyCaptureService

DISCLAIMER = (
    "This is a care documentation and reasoning support record produced by a "
    "caregiver and a local analysis tool. It is not a medical device, it makes "
    "no diagnosis, and it is not clinical assessment. Every item below is "
    "attributed to the caregiver who recorded it. Statements of uncertainty are "
    "deliberate: where the record does not establish something, it is listed as "
    "not established rather than omitted."
)


class CareSummaryBuilder:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.capture = DailyCaptureService(db)

    def build(
        self,
        person_id: str,
        window_days: int = 30,
        viewer_caregiver_id: Optional[str] = None,
        surface: str = "care_summary",
    ) -> Dict[str, Any]:
        """Assemble the summary, filtered to what this viewer may see.

        The boundary is applied inside the builder rather than at the route, so
        that no caller can forget it. That matters most for the PDF: it is the
        one surface that leaves the device, lands in an email, and gets read by
        someone outside the circle. A route that checked a flag and then called a
        builder that ignored it would look correct in review and export the lot.

        With no viewer - the local single-user case - everything is shown. That
        is a deliberate exception for the install that has one caregiver on it,
        and it is the reason the parameter is optional rather than required.
        """
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if person is None:
            raise ValueError("Person not found")

        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        caregivers = self._caregiver_names()
        allowed = self._allowed_categories(person_id, viewer_caregiver_id, surface)

        if ShareCategory.MEDICATIONS in allowed:
            adherence = self._medication_adherence(person_id, window_days)
        else:
            adherence = []

        if ShareCategory.DAILY_CARE in allowed:
            timeline = self._timeline(person_id, since, caregivers)
            observations = self._scales(person_id, since, caregivers)
            vitals = self._flagged_vitals(person_id, since, caregivers)
        else:
            timeline, observations, vitals = [], [], []

        if ShareCategory.OBSERVATIONS in allowed:
            contradictions = self._contradictions(person_id)
            gaps = self._information_gaps(person_id, window_days)
        else:
            contradictions, gaps = [], []

        if ShareCategory.FOLLOW_UPS_AND_ACTIONS in allowed:
            questions = self._follow_ups(person_id)
        else:
            questions = []

        return {
            "person": {
                "id": person.id,
                "name": person.name,
                "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None,
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_days": window_days,
            "disclaimer": DISCLAIMER,
            "care_team": caregivers,
            "timeline": timeline,
            "medication_adherence": adherence,
            "structured_observations": observations,
            "vitals_out_of_range": vitals,
            "open_contradictions": contradictions,
            "information_gaps": gaps,
            "open_questions": questions,
            "discussion_points": self._discussion_points(person_id, adherence),
            "viewing_caregiver_id": viewer_caregiver_id,
            "visible_categories": sorted(c.value for c in allowed),
            "withheld_categories": sorted(
                c.value for c in ShareCategory if c not in allowed
            ),
        }

    def _allowed_categories(
        self, person_id: str, viewer_caregiver_id: Optional[str], surface: str
    ) -> Set[ShareCategory]:
        if viewer_caregiver_id is None:
            return set(ShareCategory)
        sharing = CareSharingService(self.db)
        # Asked about explicitly rather than derived from the grant list, so the
        # access log records a decision per category. A boundary that never logs
        # cannot be audited after the fact.
        for category in ShareCategory:
            sharing.can_see(
                person_id,
                viewer_caregiver_id,
                category.value,
                surface=surface,
                log=True,
            )
        return sharing.visible_categories(person_id, viewer_caregiver_id)

    # ------------------------------------------------------------------

    def _caregiver_names(self) -> Dict[str, str]:
        """Resolve caregiver ids to names.

        A timeline of unattributed rows is a timeline nobody can act on, and a
        clinician asking "who saw this?" is the first question they usually ask.
        """
        return {c.id: c.name for c in self.db.query(Caregiver).all()}

    def _timeline(self, person_id: str, since: datetime, caregivers: Dict[str, str]) -> List[Dict[str, Any]]:
        events = (
            self.db.query(CareEvent)
            .filter(
                CareEvent.person_id == person_id,
                CareEvent.occurred_at >= since,
            )
            .order_by(CareEvent.occurred_at.desc())
            .all()
        )
        return [
            {
                "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                "recorded_by": caregivers.get(e.created_by_caregiver_id, "unattributed"),
                "event_type": e.event_type,
                "title": e.title,
                "description": e.description,
                "evidence_ids": e.evidence_ids or [],
                "tags": e.tags or [],
            }
            for e in events
        ]

    def _medication_adherence(self, person_id: str, window_days: int) -> List[Dict[str, Any]]:
        names = [
            row.medication_name
            for row in self.db.query(MedicationRecord)
            .filter(MedicationRecord.person_id == person_id)
            .distinct()
            .all()
        ]
        out = []
        for name in names:
            trend = self.capture.adherence_trend(person_id, name, window_days=window_days)
            out.append(
                {
                    "medication": name,
                    "adherence_rate": trend["adherence_rate"],
                    "given": trend["given_count"],
                    "refused": trend["refused_count"],
                    "missed": trend["missed_count"],
                    "declining": trend["declining"],
                    "evidence_ids": trend["evidence_ids"],
                    "what_is_not_established": trend["possible_context"],
                }
            )
        # Declines first: that is what the appointment is about.
        out.sort(key=lambda r: (not r["declining"], r["medication"]))
        return out

    def _scales(self, person_id: str, since: datetime, caregivers: Dict[str, str]) -> List[Dict[str, Any]]:
        rows = (
            self.db.query(ScaleObservation)
            .filter(ScaleObservation.person_id == person_id, ScaleObservation.observed_at >= since)
            .order_by(ScaleObservation.observed_at.desc())
            .all()
        )
        return [
            {
                "axis": s.axis.value,
                "score": s.score,
                "scale": "{} (1) to {} (5)".format(s.low_label, s.high_label),
                "observed_at": s.observed_at.isoformat() if s.observed_at else None,
                "recorded_by": caregivers.get(s.recorded_by_caregiver_id, "unattributed"),
                "note": s.note,
            }
            for s in rows
        ]

    def _flagged_vitals(self, person_id: str, since: datetime, caregivers: Dict[str, str]) -> List[Dict[str, Any]]:
        rows = (
            self.db.query(VitalSign)
            .filter(
                VitalSign.person_id == person_id,
                VitalSign.measured_at >= since,
                VitalSign.is_out_of_range.is_(True),
            )
            .order_by(VitalSign.measured_at.desc())
            .all()
        )
        return [
            {
                "vital": v.vital_type.value,
                "value": v.value,
                "unit": v.unit,
                "measured_at": v.measured_at.isoformat() if v.measured_at else None,
                "recorded_by": caregivers.get(v.recorded_by_caregiver_id, "unattributed"),
                "note": "Outside the usual reference range. Not a finding about a cause.",
            }
            for v in rows
        ]

    def _contradictions(self, person_id: str) -> List[Dict[str, Any]]:
        rows = (
            self.db.query(ContradictionAnalysis)
            .filter(ContradictionAnalysis.person_id == person_id, ContradictionAnalysis.is_active.is_(True))
            .order_by(ContradictionAnalysis.created_at.desc())
            .all()
        )
        return [
            {
                "summary": c.summary,
                "kind": c.primary_kind.value if c.primary_kind else None,
                "likely_explanations": c.likely_explanations or [],
                "what_to_ask": c.what_to_ask,
                "who_can_resolve": c.who_can_resolve,
                "do_not_resolve_by": c.do_not_resolve_by,
                "evidence_ids": c.evidence_ids or [],
                "possible_context": c.possible_context,
            }
            for c in rows
        ]

    def _information_gaps(self, person_id: str, window_days: int) -> List[Dict[str, Any]]:
        """What the record does not cover.

        A clinician spends a large part of a short appointment establishing what
        nobody knows. Naming the gaps here is more useful than a longer timeline
        of what does exist.
        """
        gaps: List[Dict[str, Any]] = []
        meds = self.db.query(MedicationRecord).filter(MedicationRecord.person_id == person_id).count()
        if meds == 0:
            gaps.append({
                "gap": "No medication has been recorded.",
                "why_it_matters": "Adherence cannot be discussed from this record.",
            })
        vitals = self.db.query(VitalSign).filter(VitalSign.person_id == person_id).count()
        if vitals == 0:
            gaps.append({
                "gap": "No vital signs have been recorded.",
                "why_it_matters": "Physical measurements are not available for comparison.",
            })
        scales = self.db.query(ScaleObservation).filter(ScaleObservation.person_id == person_id).count()
        if scales == 0:
            gaps.append({
                "gap": "No structured mood or behaviour observations have been recorded.",
                "why_it_matters": "Change over time can only be seen if something was written down.",
            })
        meanings = (
            self.db.query(Meaning)
            .filter(Meaning.person_id == person_id)
            .count()
        )
        if meanings == 0:
            gaps.append({
                "gap": "No interpretations have been derived from the observations.",
                "why_it_matters": "The record holds what happened, but not what it might mean.",
            })
        return gaps

    def _follow_ups(self, person_id: str) -> List[Dict[str, Any]]:
        rows = (
            self.db.query(FollowUp)
            .filter(FollowUp.person_id == person_id, FollowUp.status == "scheduled")
            .order_by(FollowUp.due_at.asc())
            .all()
        )
        return [
            {
                "what_to_recheck": f.what_to_recheck,
                "due_at": f.due_at.isoformat() if f.due_at else None,
                "why_open": f.why_open,
                "what_result_changes_the_plan": getattr(f, "what_result_changes_the_plan", None),
            }
            for f in rows
        ]

    def _discussion_points(
        self,
        person_id: str,
        adherence: List[Dict[str, Any]],
    ) -> List[str]:
        """Questions for the appointment.

        Every one is a question, not a conclusion. The caregiver is the one who
        raises them; this list exists so nothing observed gets left out of the
        conversation because the appointment was short.
        """
        points: List[str] = []

        declining = [a for a in adherence if a["declining"]]
        for item in declining:
            points.append(
                "{} adherence has fallen in the last {} days ({} given, {} refused, {} missed). "
                "Is this a side effect, a timing problem, or a change in how it is given?".format(
                    item["medication"], 30, item["given"], item["refused"], item["missed"]
                )
            )
        for item in adherence:
            if item["refused"] > 0 and not item["declining"]:
                points.append(
                    "{} has been refused {} time(s). What happens when the dose is refused?".format(
                        item["medication"], item["refused"]
                    )
                )

        vitals = self._flagged_vitals(person_id, datetime.now(timezone.utc) - timedelta(days=30), {})
        if vitals:
            seen = set()
            for v in vitals:
                if v["vital"] in seen:
                    continue
                seen.add(v["vital"])
                points.append(
                    "A {} reading of {} {} was outside the usual range. Has it been repeated or assessed?".format(
                        v["vital"].replace("_", " "), v["value"], v["unit"] or ""
                    )
                )

        for gap in self._information_gaps(person_id, 30):
            points.append(gap["gap"] + " " + gap["why_it_matters"])

        return points

