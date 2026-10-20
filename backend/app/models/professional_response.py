from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList

class ProfessionalResponse(Base):
    __tablename__ = "professional_responses"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    # Add other fields as needed
    professional_id = Column(String, nullable=True)
    response_text = Column(Text, nullable=True)
    response_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
