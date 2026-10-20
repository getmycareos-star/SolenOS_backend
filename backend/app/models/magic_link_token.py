'from sqlalchemy import Column, String, DateTime, Date, Boolean, Text, ForeignKey, Integer, Enum as SQLEnum, Index, UniqueConstraint, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.core.identity_enums import AccountType
from app.models.identity import Account

class MagicLinkToken(Base):
    """Magic link token for investor authentication."""
    __tablename__ = "magic_link_tokens"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    token = Column(String, nullable=False, unique=True, index=True)  # The actual token value
    
    # Associated investor
    investor_id = Column(String, ForeignKey("investors.id"), nullable=False, index=True)
    
    # Token metadata
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, nullable=False, default=False)
    
    # Lifecycle
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    used_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    investor = relationship("Investor", foreign_keys="MagicLinkToken.investor_id")

    __table_args__ = (
        Index("ix_magic_link_tokens_token", "token"),
        Index("ix_magic_link_tokens_expires_used", "expires_at", "used"),
    )
'
