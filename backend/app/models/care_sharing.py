"""Storage for the trust boundary between caregivers.

The design decision that everything else follows from: sharing is recorded as a
row per caregiver per category, and a category nobody has granted is absent
rather than defaulted at read time.

That ordering matters. A grant table means the question "what may this caregiver
see" is answered by looking at what someone explicitly allowed. The alternative -
one "everything" flag per caregiver, filtered at read time - makes forgetting to
exclude something the same as allowing it, and the two mistakes are not equally
recoverable.
"""

from sqlalchemy import Column, String, DateTime, Text, Boolean, ForeignKey, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.sharing_enums import (
    ShareCategory,
    ShareGrantStatus,
    ShareRelationship,
    ShareScope,
)


class CareShareGrant(Base):
    """One caregiver may see one category of one person's record."""

    __tablename__ = "care_share_grants"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    category = Column(SQLEnum(ShareCategory), nullable=False, index=True)
    scope = Column(SQLEnum(ShareScope), nullable=False, default=ShareScope.NAMED)
    status = Column(SQLEnum(ShareGrantStatus), nullable=False, default=ShareGrantStatus.ACTIVE)
    relationship = Column(SQLEnum(ShareRelationship), nullable=False, default=ShareRelationship.OTHER)

    # Why this was allowed, and who allowed it. A grant with no reason and no
    # author is indistinguishable from one nobody meant to make.
    granted_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    reason = Column(Text, nullable=True)

    granted_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Revocation is recorded, not deleted. "Who could see this and until when" is
    # the question a family will ask later, and it has no answer if the row is
    # gone.
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    revoked_reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CareShareAccessLog(Base):
    """Every time the boundary was consulted, and what it said.

    Written on each read, not only on grants changing. The failure this exists
    to make recoverable is a caregiver seeing something they should not have, and
    "who saw what" cannot be answered from grant rows alone - those record
    intention, and the question is about what actually happened.

    Deliberately not deletable through the API: there is no route that removes
    these rows.
    """

    __tablename__ = "care_share_access_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True, index=True)

    # What was asked for, and what was allowed, as separate fields. A single
    # "allowed" column loses the denied case, which is the one worth counting.
    requested_category = Column(String, nullable=False)
    granted_category = Column(SQLEnum(ShareCategory), nullable=True)

    surface = Column(String, nullable=False)
    """Where the read happened, e.g. 'care_summary', 'companion', 'pdf_export'."""

    was_allowed = Column(Boolean, nullable=False)
    denial_reason = Column(Text, nullable=True)

    accessed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CareShareDisclosure(Base):
    """What a caregiver is told about their own access, in plain terms.

    Separate from the grants because a boundary nobody understands is not a
    boundary anyone respects. Each caregiver sees their own row and nothing
    else, and the wording avoids the word "permission", which implies a
    judgement about whether they are allowed to help.
    """

    __tablename__ = "care_share_disclosures"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)

    # Acknowledged at a specific moment, because a disclosure nobody confirmed
    # being shown has not really been shown.
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
