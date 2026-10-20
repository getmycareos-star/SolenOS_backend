from sqlalchemy import Column, String, DateTime, Date, Boolean, Text, ForeignKey, Float, Integer
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList


class Caregiver(Base):
    __tablename__ = "caregivers"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Basic identification fields (maintained for backward compatibility)
    name = Column(String, nullable=False)  # Full name
    email = Column(String, unique=True, nullable=False)  # Primary email for contact/notifications
    
    # Split name fields for better formatting (derived from name if not explicitly set)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    
    # Caregiver-patient relationship
    relationship = Column(String, nullable=False)  # e.g., "daughter", "spouse", "friend", "professional"
    person_id = Column(String, ForeignKey("persons.id"), nullable=False)  # Person being cared for
    
    # Contact information
    phone_number = Column(String, nullable=True)  # Primary phone number
    email_address = Column(String, nullable=True)  # Secondary email address
    
    # Demographics and preferences
    date_of_birth = Column(DateTime(timezone=True), nullable=True)
    gender = Column(String, nullable=True)  # e.g., "male", "female", "non_binary", "prefer_not_to_say"
    language = Column(String, nullable=True, default="en")  # Preferred language
    timezone = Column(String, nullable=False, default="UTC")
    
    # Caregiver-specific information
    years_of_experience = Column(Integer, nullable=True)  # Years of caregiving experience
    employment_status = Column(String, nullable=True)  # e.g., "full_time", "part_time", "not_employed"
    is_primary_caregiver = Column(Boolean, nullable=False, default=False)  # Designated primary caregiver
    lives_with_patient = Column(Boolean, nullable=False, default=False)  # Whether caregiver lives with patient
    
    # System fields
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    is_active = Column(Boolean, nullable=False, default=True)  # Soft delete flag
    
    # Note: Authentication is handled through the Account model in identity.py
    # The link between caregiver accounts and person identities is managed through
    # AccountPersonLink records with link_type = 'caregiver_for'
