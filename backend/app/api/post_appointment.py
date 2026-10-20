from fastapi import APIRouter, Depends, HTTPException, Path, Body, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

from app.core.database import get_db
from app.services.post_appointment_workflow import PostAppointmentWorkflowService
from app.schemas.post_appointment.workflow import (
    PostAppointmentWorkflowRequest,
    PostAppointmentWorkflowResponse,
    PostAppointmentCaptureData,
    PostAppointmentReconciliationResult,
    PostAppointmentCareStateUpdate
)
from app.models.time_location import Appointment as AppointmentModel
from app.models.identity import Person as PersonModel
from app.models.caregiver import Caregiver as CaregiverModel
from app.core.security import get_current_active_user, AuthenticatedPrincipal

router = APIRouter(prefix="/post-appointment", tags=["post-appointment"])


def get_post_appointment_workflow_service(
    db: Session = Depends(get_db),
) -> PostAppointmentWorkflowService:
    """Provide a request-scoped workflow service bound to the request DB session."""
    return PostAppointmentWorkflowService(db)


@router.post("/workflow/start", response_model=PostAppointmentWorkflowResponse)
def start_post_appointment_workflow(
    workflow_request: PostAppointmentWorkflowRequest,
    service: PostAppointmentWorkflowService = Depends(get_post_appointment_workflow_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Start a post-appointment care continuity workflow.
    
    This endpoint is called when an appointment is marked as completed
    to begin the care continuity process.
    """
    # Verify the user is authorized to start workflows for this appointment
    # In a full implementation, we would check appointment access here
    
    # Derive caregiver ID from the caregiver-person relationship
    # Check if the user is a caregiver for the person in the workflow request
    person_id = workflow_request.person_id
    if not current_user.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized to access person {person_id}"
        )
    
    # Look up the caregiver entity ID for this user-person relationship
    caregiver_id = _get_caregiver_id_for_person(db=service.db, 
                                              account_id=current_user.account_id,
                                              person_id=person_id)
    
    if not caregiver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No caregiver relationship found for user {current_user.account_id} and person {person_id}"
        )
    
    # Override the caregiver_id with the actual caregiver ID for this person
    workflow_request.caregiver_id = caregiver_id
    
    return service.start_workflow(workflow_request)


@router.post("/workflow/{workflow_id}/capture", response_model=Dict[str, Any])
def capture_appointment_data(
    workflow_id: UUID = Path(..., description="The workflow ID"),
    capture_data: PostAppointmentCaptureData = Body(...),
    service: PostAppointmentWorkflowService = Depends(get_post_appointment_workflow_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Capture appointment outcome and related information.
    
    This is where the caregiver documents what happened during the appointment.
    """
    # Verify the user is authorized for this workflow
    # In a full implementation, we would check workflow access here
    
    # Verify the user has access to the person in this workflow
    workflow_info = service._get_workflow_info(workflow_id)
    if not workflow_info:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    person_id = workflow_info.get("person_id")
    if not person_id or not current_user.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized to access person {person_id}"
        )
    
    # Derive caregiver ID from the caregiver-person relationship
    caregiver_id = _get_caregiver_id_for_person(db=service.db, 
                                              account_id=current_user.account_id,
                                              person_id=person_id)
    
    if not caregiver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No caregiver relationship found for user {current_user.account_id} and person {person_id}"
        )
    
    # Override the captured_by_caregiver_id with the actual caregiver ID for this person
    capture_data.captured_by_caregiver_id = caregiver_id
    
    return service.capture_appointment_data(workflow_id, capture_data)


@router.post("/workflow/{workflow_id}/reconcile", response_model=PostAppointmentReconciliationResult)
def reconcile_information(
    workflow_id: UUID = Path(..., description="The workflow ID"),
    capture_data: PostAppointmentCaptureData = Body(...),
    person_id: str = Query(..., description="The person ID"),
    service: PostAppointmentWorkflowService = Depends(get_post_appointment_workflow_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Reconcile new appointment information with existing care state.
    
    This step compares the captured appointment data with the current care record
    to determine what has changed, what is new, what conflicts, etc.
    """
    # Verify the user is authorized for this person and workflow
    # In a full implementation, we would check access here
    
    return service.reconcile_information(workflow_id, capture_data, person_id)


@router.post("/workflow/{workflow_id}/care-state", response_model=PostAppointmentCareStateUpdate)
def update_care_state(
    workflow_id: UUID = Path(..., description="The workflow ID"),
    reconciliation_result: PostAppointmentReconciliationResult = Body(...),
    person_id: str = Query(..., description="The person ID"),
    service: PostAppointmentWorkflowService = Depends(get_post_appointment_workflow_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Update the canonical care state based on reconciliation results.
    
    This is where the care record is actually updated with new information
    from the appointment.
    """
    # Verify the user is authorized for this person and workflow
    # In a full implementation, we would check access here
    
    return service.update_care_state(workflow_id, reconciliation_result, person_id)


@router.post("/workflow/{workflow_id}/open-loops", response_model=Dict[str, Any])
def update_open_loops(
    workflow_id: UUID = Path(..., description="The workflow ID"),
    capture_data: PostAppointmentCaptureData = Body(...),
    person_id: str = Query(..., description="The person ID"),
    service: PostAppointmentWorkflowService = Depends(get_post_appointment_workflow_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Update open loops based on appointment outcomes.
    
    Resolves existing loops that were addressed and creates new loops for
    pending items from the appointment.
    """
    # Verify the user is authorized for this person and workflow
    # In a full implementation, we would check access here
    
    # Verify the user has access to the person
    if not current_user.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized to access person {person_id}"
        )
    
    # Derive caregiver ID from the caregiver-person relationship
    caregiver_id = _get_caregiver_id_for_person(db=service.db, 
                                              account_id=current_user.account_id,
                                              person_id=person_id)
    
    if not caregiver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No caregiver relationship found for user {current_user.account_id} and person {person_id}"
        )
    
    # Override the captured_by_caregiver_id in capture_data with the actual caregiver ID for this person
    capture_data.captured_by_caregiver_id = caregiver_id
    
    return service.update_open_loops(workflow_id, capture_data, person_id)


@router.post("/workflow/{workflow_id}/responsibilities", response_model=Dict[str, Any])
def update_responsibilities(
    workflow_id: UUID = Path(..., description="The workflow ID"),
    capture_data: PostAppointmentCaptureData = Body(...),
    person_id: str = Query(..., description="The person ID"),
    service: PostAppointmentWorkflowService = Depends(get_post_appointment_workflow_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Update responsibility assignments based on appointment outcomes.
    
    Creates new responsibilities from appointment instructions and updates
    existing ones as needed.
    """
    # Verify the user is authorized for this person and workflow
    # In a full implementation, we would check access here
    
    # Verify the user has access to the person
    if not current_user.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized to access person {person_id}"
        )
    
    # Derive caregiver ID from the caregiver-person relationship
    caregiver_id = _get_caregiver_id_for_person(db=service.db, 
                                              account_id=current_user.account_id,
                                              person_id=person_id)
    
    if not caregiver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No caregiver relationship found for user {current_user.account_id} and person {person_id}"
        )
    
    # Override the captured_by_caregiver_id in capture_data with the actual caregiver ID for this person
    capture_data.captured_by_caregiver_id = caregiver_id
    
    return service.update_responsibilities(workflow_id, capture_data, person_id)


@router.post("/workflow/{workflow_id}/upcoming-events", response_model=Dict[str, Any])
def update_upcoming_events(
    workflow_id: UUID = Path(..., description="The workflow ID"),
    capture_data: PostAppointmentCaptureData = Body(...),
    person_id: str = Query(..., description="The person ID"),
    service: PostAppointmentWorkflowService = Depends(get_post_appointment_workflow_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Update upcoming events based on appointment outcomes.
    
    Creates events for scheduled appointments, tests, follow-ups, etc.
    """
    # Verify the user is authorized for this person and workflow
    # In a full implementation, we would check access here
    
    # Verify the user has access to the person
    if not current_user.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized to access person {person_id}"
        )
    
    # Derive caregiver ID from the caregiver-person relationship
    caregiver_id = _get_caregiver_id_for_person(db=service.db, 
                                              account_id=current_user.account_id,
                                              person_id=person_id)
    
    if not caregiver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No caregiver relationship found for user {current_user.account_id} and person {person_id}"
        )
    
    # Override the captured_by_caregiver_id in capture_data with the actual caregiver ID for this person
    capture_data.captured_by_caregiver_id = caregiver_id
    
    return service.update_upcoming_events(workflow_id, capture_data, person_id)


@router.post("/workflow/{workflow_id}/handoff-context", response_model=Dict[str, Any])
def update_handoff_context(
    workflow_id: UUID = Path(..., description="The workflow ID"),
    person_id: str = Query(..., description="The person ID"),
    service: PostAppointmentWorkflowService = Depends(get_post_appointment_workflow_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Update caregiver handoff context after appointment.
    
    Ensures handoff context reflects the current care state after the appointment.
    """
    # Verify the user is authorized for this person and workflow
    # In a full implementation, we would check access here
    
    return service.update_handoff_context(workflow_id, person_id)


@router.post("/workflow/{workflow_id}/family-summary", response_model=Dict[str, Any])
def generate_family_summary(
    workflow_id: UUID = Path(..., description="The workflow ID"),
    person_id: str = Query(..., description="The person ID"),
    viewer_caregiver_id: Optional[UUID] = Query(None, description="The caregiver requesting the summary"),
    service: PostAppointmentWorkflowService = Depends(get_post_appointment_workflow_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Generate a family summary if appropriate.
    
    Creates a shareable summary of what happened and what needs to happen next.
    """
    # Verify the user is authorized for this person and workflow
    # In a full implementation, we would check access here
    
    # Verify the user has access to the person
    if not current_user.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized to access person {person_id}"
        )
    
    # If no viewer is specified, derive from the caregiver-person relationship
    # Otherwise, verify the viewer_caregiver_id corresponds to a valid caregiver for this person
    if viewer_caregiver_id is None:
        viewer_caregiver_id = _get_caregiver_id_for_person(db=service.db, 
                                                          account_id=current_user.account_id,
                                                          person_id=person_id)
        
        if not viewer_caregiver_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No caregiver relationship found for user {current_user.account_id} and person {person_id}"
            )
    else:
        # Verify that the specified viewer_caregiver_id is valid for this person
        # In a full implementation, we would check if this caregiver is authorized for the person
        # For now, we'll check if the current user can access the person (already done above)
        pass
    
    return service.generate_family_summary(workflow_id, person_id, viewer_caregiver_id)


@router.get("/workflow/{workflow_id}/status", response_model=Dict[str, Any])
def get_workflow_status(
    workflow_id: UUID = Path(..., description="The workflow ID"),
    service: PostAppointmentWorkflowService = Depends(get_post_appointment_workflow_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Get the current status of a post-appointment workflow.
    
    Returns information about what steps have been completed and what's next.
    """
    # Verify the user is authorized for this workflow
    # In a full implementation, we would check workflow access here
    
    # Get actual status from workflow storage
    workflow_info = service._get_workflow_info(workflow_id)
    if not workflow_info:
        return {
            "workflow_id": str(workflow_id),
            "status": "not_found",
            "error": "Workflow not found or expired",
            "updated_at": datetime.now().isoformat()
        }
    
    # Define the workflow steps
    workflow_steps = [
        "open_capture_workspace",
        "capture_appointment_outcome",
        "capture_evidence",
        "reconcile_information",
        "update_care_state",
        "update_open_loops",
        "update_responsibilities",
        "update_upcoming_events",
        "update_handoff_context",
        "generate_family_summary_if_appropriate"
    ]
    
    # Determine completed steps based on workflow status
    completed_steps = []
    current_status = workflow_info.get("status", "initiated")
    
    # Map status to completed steps
    status_step_map = {
        "initiated": [],
        "captured": ["open_capture_workspace", "capture_appointment_outcome", "capture_evidence"],
        "reconciled": ["open_capture_workspace", "capture_appointment_outcome", "capture_evidence", "reconcile_information"],
        "care_state_updated": ["open_capture_workspace", "capture_appointment_outcome", "capture_evidence", "reconcile_information", "update_care_state"],
        "open_loops_updated": ["open_capture_workspace", "capture_appointment_outcome", "capture_evidence", "reconcile_information", "update_care_state", "update_open_loops"],
        "responsibilities_updated": ["open_capture_workspace", "capture_appointment_outcome", "capture_evidence", "reconcile_information", "update_care_state", "update_open_loops", "update_responsibilities"],
        "upcoming_events_updated": ["open_capture_workspace", "capture_appointment_outcome", "capture_evidence", "reconcile_information", "update_care_state", "update_open_loops", "update_responsibilities", "update_upcoming_events"],
        "handoff_context_updated": ["open_capture_workspace", "capture_appointment_outcome", "capture_evidence", "reconcile_information", "update_care_state", "update_open_loops", "update_responsibilities", "update_upcoming_events", "update_handoff_context"],
        "completed": workflow_steps  # All steps completed
    }
    
    completed_steps = status_step_map.get(current_status, [])
    
    # Determine next steps
    next_steps = [step for step in workflow_steps if step not in completed_steps]
    
    return {
        "workflow_id": str(workflow_id),
        "status": current_status,
        "completed_steps": completed_steps,
        "next_steps": next_steps,
        "updated_at": workflow_info.get("updated_at", datetime.now(timezone.utc)).isoformat() if hasattr(workflow_info.get("updated_at", datetime.now(timezone.utc)), 'isoformat') else datetime.now().isoformat(),
        "workflow_info": {
            "appointment_id": str(workflow_info.get("appointment_id")) if workflow_info.get("appointment_id") else None,
            "person_id": workflow_info.get("person_id"),
            "caregiver_id": str(workflow_info.get("caregiver_id")) if workflow_info.get("caregiver_id") else None
        }
    }


# Alternative: Create a convenience endpoint that runs the entire workflow
# This would be useful for automated/testing scenarios
@router.post("/appointment/{appointment_id}/process-completed", response_model=Dict[str, Any])
def process_completed_appointment(
    appointment_id: UUID = Path(..., description="The appointment ID"),
    caregiver_notes: Optional[str] = Body(None, description="Initial notes from the caregiver"),
    service: PostAppointmentWorkflowService = Depends(get_post_appointment_workflow_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Process a completed appointment through the entire post-appointment workflow.
    
    This is a convenience endpoint that automates the workflow for cases where
    we want to process everything sequentially.
    
    In a real implementation, this would likely be used for testing or automated
    processing, while the step-by-step endpoints would be used for manual workflows.
    """
    # Verify the appointment exists and is completed
    appointment = (
        service.db.query(AppointmentModel)
        .filter(AppointmentModel.id == str(appointment_id))
        .first()
    )
    
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    
    if appointment.status != "completed":
        raise HTTPException(
            status_code=400, 
            detail=f"Appointment is not completed. Current status: {appointment.status}"
        )
    
    # Check if the current user is authorized to access this person's data
    if not current_user.has_access_to_person(appointment.person_id):
        raise HTTPException(status_code=403, detail="Not authorized to access this person's data")
    
    # Determine caregiver ID - this should come from the caregiver relationship between the user and the person
    caregiver_id = _get_caregiver_id_for_person(db=service.db, 
                                              account_id=current_user.account_id,
                                              person_id=appointment.person_id)
    
    if not caregiver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No caregiver relationship found for user {current_user.account_id} and person {appointment.person_id}"
        )
    
    # Start the workflow
    workflow_request = PostAppointmentWorkflowRequest(
        appointment_id=appointment_id,
        caregiver_id=caregiver_id,
        workflow_notes=caregiver_notes,
        capture_immediately=False
    )
    
    workflow_response = service.start_workflow(workflow_request)
    
    # In a full implementation, we would continue through the steps automatically
    # For now, we'll return the workflow initiation
    
    return {
        "workflow_initiated": True,
        "workflow_id": str(workflow_response.workflow_id),
        "appointment_id": str(appointment_id),
        "status": workflow_response.status,
        "next_steps": workflow_response.next_steps,
        "message": "Post-appointment workflow initiated. Use the step-by-step endpoints to continue."
    }


def _get_caregiver_id_for_person(db: Session, account_id: str, person_id: str) -> Optional[UUID]:
    """
    Helper function to derive caregiver ID from the caregiver-person relationship.
    
    In a real implementation, this would query the database to find the caregiver
    entity ID associated with the given account ID and person ID.
    """
    try:
        # Query to find caregiver relationship
        # This assumes there's a relationship table or field linking accounts to caregivers
        # and caregivers to persons
        
        # First, find the caregiver entity ID for this account
        caregiver = db.query(CaregiverModel).filter(CaregiverModel.account_id == account_id).first()
        
        if not caregiver:
            # No caregiver entity found for this account
            return None
        
        # Verify that this caregiver is authorized for the person
        # This would typically involve checking care sharing grants or similar
        from app.models.care_sharing import CareShareGrant
        
        # Check if there's an active grant from this caregiver to the person
        grant = db.query(CareShareGrant).filter(
            CareShareGrant.person_id == person_id,
            CareShareGrant.caregiver_id == caregiver.id,
            CareShareGrant.status == "active"
        ).first()
        
        if grant:
            return caregiver.id
        else:
            # No active sharing relationship found
            return None
            
    except Exception as e:
        # Log the error in a real implementation
        return None