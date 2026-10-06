"""The trust boundary between caregivers.

One function matters here: `can_see`. Everything else exists to make its answer
defensible and checkable.

The rule it enforces is asymmetric on purpose. Absent a grant, the answer is no.
That is the safe default for a medical record, and the cost of getting it wrong
is not symmetric either way: under-sharing is one message and a tap to fix,
while over-sharing of a GP letter cannot be recalled from someone's phone.

Three properties the implementation is built to keep:

- No silent widening. A category is visible only when a live grant exists for
  it. Nothing is inherited from a role, a relationship, or a previous wider
  grant.
- Every decision is logged. Denials as well as grants. A boundary whose denials
  leave no trace cannot be audited, and "who saw what" is the question a family
  will actually ask.
- Revocation is immediate and total. There is no cache, no session copy and no
  read-through that could serve a revoked category.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy.orm import Session

from app.core.sharing_enums import (
    DEFAULT_CATEGORY,
    FAMILY_ONLY_CATEGORIES,
    NON_FAMILY_LIMITED_CATEGORIES,
    SHAREABLE_CATEGORIES,
    ShareCategory,
    ShareGrantStatus,
    ShareRelationship,
    ShareScope,
)
from app.models.care_sharing import (
    CareShareAccessLog,
    CareShareDisclosure,
    CareShareGrant,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class ShareDenied(Exception):
    """Raised when something asks for a category this caregiver does not hold.

    Carries the reason so the caller can tell a caregiver what is missing
    without the caller having to reimplement the rules.
    """

    def __init__(self, reason: str, category: Optional[str] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.category = category


class CareSharingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Grants
    # ------------------------------------------------------------------

    def grant(
        self,
        person_id: str,
        caregiver_id: str,
        category: str,
        granted_by_caregiver_id: Optional[str] = None,
        relationship: str = ShareRelationship.OTHER.value,
        scope: str = ShareScope.NAMED.value,
        reason: Optional[str] = None,
        expires_in_days: Optional[int] = None,
    ) -> CareShareGrant:
        """Allow one caregiver to see one category.

        Refuses rather than widening when the request does not make sense, since
        silently granting a neighbour the GP letters because the API accepted the
        string is the exact harm this module exists to prevent.
        """
        category_value = ShareCategory(category)

        if category_value not in SHAREABLE_CATEGORIES:
            raise ValueError("this category cannot be shared")

        relationship_value = ShareRelationship(relationship)
        scope_value = ShareScope(scope)

        if category_value in FAMILY_ONLY_CATEGORIES and relationship_value not in (
            ShareRelationship.PRIMARY_FAMILY,
            ShareRelationship.SECONDARY_FAMILY,
        ):
            raise ValueError(
                f"{category_value.value} can only be shared with family caregivers"
            )

        if relationship_value not in (
            ShareRelationship.PRIMARY_FAMILY,
            ShareRelationship.SECONDARY_FAMILY,
        ) and category_value not in NON_FAMILY_LIMITED_CATEGORIES:
            raise ValueError(
                f"a {relationship_value.value} caregiver may not be given {category_value.value}"
            )

        # A group grant is a way of saying "everyone, including whoever is added
        # next month". For anything a clinician wrote, that is never the intent.
        if category_value in FAMILY_ONLY_CATEGORIES and scope_value is not ShareScope.NAMED:
            raise ValueError(
                f"{category_value.value} must be granted to named caregivers, not the whole circle"
            )

        expires_at = None
        if expires_in_days is not None:
            if expires_in_days <= 0:
                raise ValueError("expires_in_days must be positive")
            expires_at = _utc_now() + timedelta(days=int(expires_in_days))

        row = CareShareGrant(
            person_id=person_id,
            caregiver_id=caregiver_id,
            category=category_value,
            scope=scope_value,
            status=ShareGrantStatus.ACTIVE,
            relationship=relationship_value,
            granted_by_caregiver_id=granted_by_caregiver_id,
            reason=reason,
            expires_at=expires_at,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def revoke(
        self,
        grant_id: str,
        revoked_by_caregiver_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> CareShareGrant:
        row = self.db.query(CareShareGrant).filter(CareShareGrant.id == grant_id).first()
        if row is None:
            raise ValueError("no such grant")
        # Revoking twice is not an error. A family member may press the button
        # twice, and a second call that failed would suggest the first did not
        # work.
        if row.status is ShareGrantStatus.REVOKED:
            return row
        row.status = ShareGrantStatus.REVOKED
        row.revoked_at = _utc_now()
        row.revoked_by_caregiver_id = revoked_by_caregiver_id
        row.revoked_reason = reason
        self.db.flush()
        return row

    def grants_for(self, person_id: str, caregiver_id: Optional[str] = None) -> List[CareShareGrant]:
        query = self.db.query(CareShareGrant).filter(CareShareGrant.person_id == person_id)
        if caregiver_id is not None:
            query = query.filter(CareShareGrant.caregiver_id == caregiver_id)
        return query.order_by(CareShareGrant.granted_at.desc()).all()

    # ------------------------------------------------------------------
    # The boundary
    # ------------------------------------------------------------------

    def live_grants(self, person_id: str, caregiver_id: str) -> List[CareShareGrant]:
        """Active, unexpired grants. Expiry is evaluated here, not stored.

        A grant whose expiry has passed is not modified, so the record still
        shows that it was given and when it lapsed, which is what a family will
        ask about later.
        """
        now = _utc_now()
        live = []
        for row in self.grants_for(person_id, caregiver_id):
            if row.status is not ShareGrantStatus.ACTIVE:
                continue
            expires = _aware(row.expires_at)
            if expires is not None and expires <= now:
                continue
            live.append(row)
        return live

    def can_see(
        self,
        person_id: str,
        caregiver_id: Optional[str],
        category: str,
        surface: str = "unknown",
        log: bool = True,
    ) -> bool:
        """May this caregiver see this category? The one function that decides.

        Every read in the system goes through here, and the answer is written to
        the access log both ways, so the denial count is as visible as the
        grant count.
        """
        if caregiver_id is None:
            # An unauthenticated caller gets nothing. Recording it as a denial
            # rather than returning early keeps the attempt in the trail.
            self._log(
                person_id=person_id,
                caregiver_id=None,
                requested_category=category,
                granted=None,
                surface=surface,
                allowed=False,
                reason="no caregiver identified",
            )
            return False

        try:
            category_value = ShareCategory(category)
        except ValueError:
            self._log(
                person_id=person_id,
                caregiver_id=caregiver_id,
                requested_category=str(category),
                granted=None,
                surface=surface,
                allowed=False,
                reason="unknown category",
            )
            return False

        granted = None
        for row in self.live_grants(person_id, caregiver_id):
            if row.category is category_value:
                granted = row
                break

        if granted is None:
            self._log(
                person_id=person_id,
                caregiver_id=caregiver_id,
                requested_category=category_value.value,
                granted=None,
                surface=surface,
                allowed=False,
                reason="no active grant for this category",
            )
            return False

        if log:
            self._log(
                person_id=person_id,
                caregiver_id=caregiver_id,
                requested_category=category_value.value,
                granted=category_value,
                surface=surface,
                allowed=True,
                reason=None,
            )
        return True

    def require(
        self, person_id: str, caregiver_id: Optional[str], category: str, surface: str = "unknown"
    ) -> None:
        """Raise unless allowed. For callers that should not proceed at all."""
        if not self.can_see(person_id, caregiver_id, category, surface=surface):
            raise ShareDenied(
                "this caregiver does not have access to " + str(category), category=str(category)
            )

    def visible_categories(self, person_id: str, caregiver_id: Optional[str]) -> Set[ShareCategory]:
        """What this caregiver can see, computed from grants only.

        Note this takes no logging path: it is used to build views, and a view
        that asked about every category to draw itself would fill the access log
        with the whole schema on every page load. The reads that follow are
        logged individually.
        """
        return {row.category for row in self.live_grants(person_id, caregiver_id)}

    def _log(
        self,
        person_id: str,
        caregiver_id: Optional[str],
        requested_category: str,
        granted: Optional[ShareCategory],
        surface: str,
        allowed: bool,
        reason: Optional[str],
    ) -> None:
        entry = CareShareAccessLog(
            person_id=person_id,
            caregiver_id=caregiver_id,
            requested_category=str(requested_category),
            granted_category=granted,
            surface=surface,
            was_allowed=allowed,
            denial_reason=reason,
        )
        self.db.add(entry)
        self.db.flush()

    # ------------------------------------------------------------------
    # Disclosure
    # ------------------------------------------------------------------

    def disclosure_for(self, caregiver_id: str) -> Dict[str, Any]:
        """What this caregiver is told about their own access.

        Only ever returns the caller's own row. There is no parameter for
        looking at anyone else's.
        """
        people = (
            self.db.query(CareShareGrant.person_id)
            .filter(CareShareGrant.caregiver_id == caregiver_id)
            .distinct()
            .all()
        )
        per_person = []
        for (person_id,) in people:
            categories = sorted(c.value for c in self.visible_categories(person_id, caregiver_id))
            per_person.append(
                {
                    "person_id": person_id,
                    "categories": categories,
                    "summary": _plain_summary(categories),
                }
            )
        return {
            "caregiver_id": caregiver_id,
            "people": per_person,
            "_note": (
                "This is what you can see, and you can ask for more at any time. "
                "Anything a clinician has written stays with the family unless they "
                "choose to share it."
            ),
        }

    def acknowledge_disclosure(self, caregiver_id: str, person_id: str) -> CareShareDisclosure:
        row = (
            self.db.query(CareShareDisclosure)
            .filter(
                CareShareDisclosure.caregiver_id == caregiver_id,
                CareShareDisclosure.person_id == person_id,
            )
            .first()
        )
        if row is None:
            row = CareShareDisclosure(person_id=person_id, caregiver_id=caregiver_id)
            self.db.add(row)
        row.acknowledged_at = _utc_now()
        self.db.flush()
        return row

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def access_log(
        self, person_id: str, limit: int = 100, only_denied: bool = False
    ) -> List[Dict[str, Any]]:
        query = self.db.query(CareShareAccessLog).filter(
            CareShareAccessLog.person_id == person_id
        )
        if only_denied:
            query = query.filter(CareShareAccessLog.was_allowed.is_(False))
        rows = query.order_by(CareShareAccessLog.accessed_at.desc()).limit(limit).all()
        return [
            {
                "id": r.id,
                "caregiver_id": r.caregiver_id,
                "requested_category": r.requested_category,
                "granted_category": r.granted_category.value if r.granted_category else None,
                "surface": r.surface,
                "was_allowed": r.was_allowed,
                "denial_reason": r.denial_reason,
                "accessed_at": _aware(r.accessed_at).isoformat(),
            }
            for r in rows
        ]


# What each category is called in front of a caregiver. A caregiver who is told
# "clinical_documents" learns nothing about whether that includes their mother's
# diagnosis; one who is told "letters from the doctor and test results" can
# decide for themselves.
_CATEGORY_PHRASES = {
    ShareCategory.DAILY_CARE: "what has happened each day, including medication and sleep",
    ShareCategory.MEDICATIONS: "the medicines she takes and how they are going",
    ShareCategory.CLINICAL_DOCUMENTS: "letters from the doctor and test results",
    ShareCategory.OBSERVATIONS: "observations other carers have shared",
    ShareCategory.FOLLOW_UPS_AND_ACTIONS: "appointments and things agreed to do",
    ShareCategory.MEANING_AND_WHAT_MATTERS: "what matters to her, in her own words",
}


def _plain_summary(categories: Sequence[str]) -> str:
    if not categories:
        return "Nothing has been shared with you yet."
    phrases = [
        _CATEGORY_PHRASES[ShareCategory(c)]
        for c in categories
        if c in {p.value for p in ShareCategory}
    ]
    if not phrases:
        return "Nothing has been shared with you yet."
    return "You can see " + "; ".join(phrases) + "."


def default_categories_for(relationship: str) -> Set[ShareCategory]:
    """The starting position for a caregiver, used when onboarding a circle.

    A starting point, not an entitlement. Family start with what the family
    needs to coordinate care; everyone starts with the day-to-day operational
    view and nothing more sensitive. It is deliberately below what most people
    would grant, because a grant given with a reason is easy and a grant given by
    default is hard to review.
    """
    if relationship in (
        ShareRelationship.PRIMARY_FAMILY.value,
        ShareRelationship.SECONDARY_FAMILY.value,
    ):
        return {
            ShareCategory.DAILY_CARE,
            ShareCategory.MEDICATIONS,
            ShareCategory.FOLLOW_UPS_AND_ACTIONS,
        }
    return {DEFAULT_CATEGORY}
