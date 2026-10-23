from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, JSON, String, func

from app.core.database import Base


class OpsEvent(Base):
    """Minimal, non-clinical operational event stream."""

    __tablename__ = "solen_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    user_id_hash = Column(String(64), nullable=True, index=True)
    event_name = Column(String(80), nullable=False, index=True)
    session_id_hash = Column(String(64), nullable=False, index=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
