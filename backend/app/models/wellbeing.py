"""Storage for caregiver wellbeing.

The rule that shapes this file: a caregiver's own state is stored against the
caregiver, and never as evidence about the person being cared for.

The daily-care form already accepts a stress level, and it writes it as a
CareEvent on the person. That is a category error rather than a small
inconvenience: the person did not report stress, the caregiver did, and a
clinical summary that said "Margaret: stress 4/5" would be inventing a symptom
for her. Worse, anything recorded about a caregiver that lands in the person's
record is shared with every caregiver on the care circle by default, which is
not something anyone agreed to when they tapped a button at eleven at night.

So these rows carry a caregiver id, are excluded from every person-facing query
in the codebase, and are read only by this service.
"""

from sqlalchemy import Column, String, DateTime, Text, Boolean, Integer, ForeignKey, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.wellbeing_enums import WellbeingAxis, WellbeingDirection


class WellbeingCheckIn(Base):
    """One thing the caregiver said about themselves, at one moment.

    Not a survey. A caregiver answering this is taking thirty seconds between
    things they had to do, so the model is one axis at a time and nothing is
    required.
    """

    __tablename__ = "wellbeing_check_ins"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # The caregiver, deliberately, not the person. See the module docstring.
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    axis = Column(SQLEnum(WellbeingAxis), nullable=False, index=True)
    score = Column(Integer, nullable=False)
    direction = Column(SQLEnum(WellbeingDirection), nullable=False)

    # The words shown on the button at the time. Kept because "3" means nothing
    # in three months without them, and because the poles may be reworded.
    low_label = Column(String, nullable=True)
    high_label = Column(String, nullable=True)

    # Optional free text, kept verbatim. Never summarised or rewritten.
    note = Column(Text, nullable=True)

    checked_in_at = Column(DateTime(timezone=True), nullable=False, index=True)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class WellbeingObservation(Base):
    """Something the system noticed across a caregiver's own check-ins.

    Named "observation" rather than "insight" because it is arithmetic, not
    understanding: three high stress scores in a row is a pattern in the data
    and nothing more. It carries the check-ins it was derived from so a
    caregiver can see the basis and disagree with it.
    """

    __tablename__ = "wellbeing_observations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    signal = Column(String, nullable=False, index=True)

    # Stated in the second person and about the data, never as a judgement about
    # the person. "Three of your last four stress check-ins were 4 or 5."
    statement = Column(Text, nullable=False)

    # The basis, so it can be checked and so a caregiver can reject it.
    basis = Column(Text, nullable=True)
    source_check_in_ids = Column(Text, nullable=True)

    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)

    # A caregiver can dismiss an observation. Dismissal is respected permanently
    # rather than re-surfaced, because a system that keeps showing you something
    # you dismissed has not listened and is exhausting to use.
    is_dismissed = Column(Boolean, nullable=False, default=False)
    dismissed_at = Column(DateTime(timezone=True), nullable=True)
    dismissed_note = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SupportAction(Base):
    """Something the caregiver can do about a burden, offered not prescribed.

    Suggestions are concrete and about logistics, never clinical. "Ask someone to
    take Thursday" is something a person can do tonight. "Consider respite care"
    is a judgement this system has no basis to make from four check-ins.
    """

    __tablename__ = "wellbeing_support_actions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    kind = Column(String, nullable=False)
    description = Column(Text, nullable=False)

    # Which people the suggestion concerns, so a shared rota can be offered to
    # the specific sibling who lives nearby rather than to the whole circle.
    related_caregiver_ids = Column(Text, nullable=True)
    offered_at = Column(DateTime(timezone=True), nullable=False, index=True)
    is_taken = Column(Boolean, nullable=False, default=False)
    taken_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
