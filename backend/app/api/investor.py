from fastapi import APIRouter
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from fastapi import Request, Depends
from app.core.database import get_db
from app.models.investor import Investor
from pydantic import BaseModel

# In-memory storage for magic link tokens (in production, use Redis or database)
_magic_link_tokens = {}

class MagicLinkRequest(BaseModel):
    email: str

class MagicLinkResponse(BaseModel):
    success: bool
    data: dict

router = APIRouter()


@router.post("/magic-link", response_model=MagicLinkResponse)
def request_magic_link(
    request: Request,
    magic_link_request: MagicLinkRequest,
    db: Session = Depends(get_db),
):
    """Investor endpoint to request a magic link."""
    # Normalize email (lowercase, strip)
    email = magic_link_request.email.strip().lower()
    
    # Find investor by email (we need to join with Account to get Investor record)
    # Query: Find Investor where Investor.email == email and is_approved=True and is_active=True
    investor = db.query(Investor).filter(
        Investor.email == email,
        Investor.is_approved == True,
        Investor.is_active == True
    ).first()
    
    # To prevent email enumeration, always return the same message
    # In reality, we would only send if investor exists and is approved/active
    
    if investor:
        # Generate a magic link token
        token = str(uuid4())
        # Set expiry to 15 minutes from now
        expires_at = datetime.now(timezone) + timedelta(minutes=15)
        
        # Store token
        _magic_link_tokens[token] = {
            "investor_id": investor.id,
            "expires_at": expires_at,
            "used": False
        }
        
        # Log token to console (simulating email sending)
        # In production, replace with actual email sending logic
        print(f"[DEV] Magic link token for {email}: {token}")
        print(f"[DEV] Token expires at: {expires_at.isoformat()}")
        
        # In a real implementation, we would send the token via email here
        # For example: send_email(to=email, subject="Your SolenOS Investor Magic Link", body=f"Use this token: {token}")
    
    # Always return the same message to prevent email enumeration
    return {
        "success": True,
        "data": {
            "message": "If the email exists and is approved, a magic link has been sent."
        }
    }