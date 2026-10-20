from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional

from app.core.database import get_db
from app.core.security import get_current_principal, AuthenticatedPrincipal, require_authenticated
from app.crud.notification import (
    get_notification_by_id,
    get_notifications_by_recipient,
    create_notification,
    update_notification,
    get_unread_count_by_recipient
)
from app.schemas.notification import NotificationCreate, NotificationUpdate, Notification, NotificationUnreadCount

router = APIRouter(prefix="/person/{person_id}/notifications", tags=["notifications"])

def verify_person_access(
    person_id: str = Path(...), 
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    """Verify that the authenticated principal has access to the specified person_id."""
    require_authenticated(principal)
    if person_id not in principal.authorized_person_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied to person {person_id}"
        )
    return principal

@router.get("", response_model=List[Notification])
def get_notifications(
    person_id: str = Path(...),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    unread_only: bool = Query(False),
    principal: AuthenticatedPrincipal = Depends(verify_person_access),
    db: Session = Depends(get_db)
):
    """
    Get notifications for a person.
    """
    notifications = get_notifications_by_recipient(
        db, 
        recipient_person_id=person_id, 
        skip=skip, 
        limit=limit,
        unread_only=unread_only
    )
    return notifications

@router.get("/unread-count", response_model=NotificationUnreadCount)
def get_unread_count(
    person_id: str = Path(...),
    principal: AuthenticatedPrincipal = Depends(verify_person_access),
    db: Session = Depends(get_db)
):
    """
    Get the unread notification count for a person.
    """
    count = get_unread_count_by_recipient(db, recipient_person_id=person_id)
    return NotificationUnreadCount(unread_count=count)

@router.post("", response_model=Notification, status_code=status.HTTP_201_CREATED)
def create_notification_endpoint(
    person_id: str = Path(...),
    notification: NotificationCreate,
    principal: AuthenticatedPrincipal = Depends(verify_person_access),
    db: Session = Depends(get_db)
):
    """
    Create a new notification for a person.
    """
    # Ensure that the notification is for the same person as in the URL
    if notification.recipient_person_id != person_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notification recipient_person_id must match the person_id in the URL"
        )
    return create_notification(db, notification)

@router.patch("/{notification_id}", response_model=Notification)
def update_notification_endpoint(
    person_id: str = Path(...),
    notification_id: int = Path(..., gt=0),
    notification_update: NotificationUpdate,
    principal: AuthenticatedPrincipal = Depends(verify_person_access),
    db: Session = Depends(get_db)
):
    """
    Update a notification (e.g., mark as read, archive).
    """
    # First, get the notification to ensure it belongs to the person
    db_notification = get_notification_by_id(db, notification_id)
    if not db_notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification with id {notification_id} not found"
        )
    if db_notification.recipient_person_id != person_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied to notification {notification_id}"
        )
    return update_notification(db, notification_id, notification_update)
