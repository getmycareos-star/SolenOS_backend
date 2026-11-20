from sqlalchemy import Column, DateTime, Index, Integer, String

from app.core.database import Base


class OperationalWorkerHeartbeat(Base):
    """Persist the latest completion state for each durable background worker."""

    __tablename__ = "operational_worker_heartbeats"
    __table_args__ = (
        Index("ix_operational_worker_heartbeats_last_success_at", "last_success_at"),
    )

    worker_name = Column(String(100), primary_key=True)
    last_started_at = Column(DateTime(timezone=True), nullable=False)
    last_completed_at = Column(DateTime(timezone=True), nullable=False)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_outcome = Column(String(20), nullable=False)
    last_error_type = Column(String(120), nullable=True)
    last_processed_count = Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime(timezone=True), nullable=False)
