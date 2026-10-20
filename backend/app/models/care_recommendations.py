from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList

class CareRecommendation(Base):
    __tablename__ = "care_recommendations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    # Add other fields as needed
    recommendation_text = Column(Text, nullable=False)
    recommendation_type = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
