from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.care_context_enums import TrajectoryDirection


class TrajectoryPoint(Base):
    """A single dated reading of a tracked measure.

    Kept as points rather than pre-aggregated so a trajectory can be recomputed
    when a late-arriving reading changes the shape. Aggregation hides the third
    data point, and the number of data points is the thing that decides whether a
    reading is worth anything.
    """

    __tablename__ = "trajectory_points"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    trajectory_id = Column(String, ForeignKey("trajectories.id"), nullable=True, index=True)
    subject = Column(String, nullable=False, index=True)
    value = Column(Float, nullable=True)
    value_text = Column(Text, nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    observed_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    source_text = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    is_estimate = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Trajectory(Base):
    """Where a tracked measure is heading, and how much of that is real.

    Direction and slope are computed deterministically from the points. Only the
    interpretation of the shape is left to the language model, and it cannot
    change the direction that was computed.
    """

    __tablename__ = "trajectories"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject = Column(String, nullable=False, index=True)
    label = Column(String, nullable=False)
    unit = Column(String, nullable=True)

    direction = Column(SQLEnum(TrajectoryDirection), nullable=False, default=TrajectoryDirection.INSUFFICIENT_DATA)
    # Whether a rise in the measure is bad news. Weight falling is a decline;
    # pain rising is a decline. Without this the engine would report both as
    # improvement, so it is recorded rather than guessed.
    higher_is_worse = Column(Boolean, nullable=False, default=False)
    slope_per_week = Column(Float, nullable=True)
    spread = Column(Float, nullable=True)
    point_count = Column(Integer, nullable=False, default=0)
    window_start = Column(DateTime(timezone=True), nullable=True)
    window_end = Column(DateTime(timezone=True), nullable=True)
    is_volatile = Column(Boolean, default=False)

    plain_reading = Column(Text, nullable=True)
    what_it_could_mean = Column(JSONList, nullable=True)
    if_it_continues = Column(Text, nullable=True)
    worth_watching_for = Column(Text, nullable=True)

    baseline_variation_explains = Column(Boolean, nullable=True)
    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)

    is_active = Column(Boolean, default=True)
    superseded_by_trajectory_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
