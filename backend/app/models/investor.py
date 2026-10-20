from sqlalchemy import Column, String, DateTime, Date, Boolean, Text, ForeignKey, Integer, Enum as SQLEnum, Index, UniqueConstraint, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.core.identity_enums import AccountType
from app.models.identity import Account

class Investor(Base):
    """Investor account - for external investors to access approved metrics."""
    __tablename__ = "investors"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("accounts.id"), nullable=False, unique=True)
    
    # Investor information
    email = Column(String, nullable=False)  # Normalized email
    display_name = Column(String, nullable=True)
    
    # Investor status
    is_approved = Column(Boolean, nullable=False, default=False)  # Founder approval
    is_active = Column(Boolean, nullable=False, default=True)    # Account not suspended/revoked
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # Optional access expiration
    
    # Metric permissions (list of metric group identifiers)
    metric_permissions = Column(JSONList, nullable=False, default=[])  # e.g., ["company_overview", "product_adoption"]
    
    # Security
    is_blocked = Column(Boolean, nullable=False, default=False)
    blocked_at = Column(DateTime(timezone=True), nullable=True)
    blocked_reason = Column(Text, nullable=True)
    
    # Lifecycle
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    account = relationship("Account", foreign_keys="Investor.account_id")
    # approver = relationship("Account", foreign_keys="Investor.approved_by_account_id")

    __table_args__ = (
        Index("ix_investors_email", "email"),
        Index("ix_investors_approved_active", "is_approved", "is_active"),
    )
