import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from unittest.mock import MagicMock

import structlog
from sqlalchemy.orm import Session

from app.models.time_location import Appointment as AppointmentModel
from app.models.care_event import CareEvent
from app.models.evidence import Evidence
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.core.care_context_enums import HandoffReason
from app.services.care_common import utc_now
from app.services.follow_up import FollowUpEngine
from app.services.caregiver_intelligence import CaregiverIntelligenceService, CaregiverResponsibilityCreate, ResponsibilityStatus
from app.models.handoff import Handoff, HandoffResponsibility
from app.schemas.post_appointment.workflow import (
    PostAppointmentWorkflowRequest,
    PostAppointmentCaptureData,
    PostAppointmentWorkflowResponse,
    PostAppointmentReconciliationResult,
    PostAppointmentCareStateUpdate,
)

# In-memory storage for workflow states (temporary, for demo purposes)
_WORKFLOW_STORAGE: Dict[uuid.UUID, Dict[str, Any]] = {}

logger = structlog.get_logger()


class PostAppointmentWorkflowService:
    def __init__(self, db: Session):
        self.db = db

    def initiate_workflow(
        self, 
        workflow_request: PostAppointmentWorkflowRequest,
        person_id: str,
        caregiver_id: str
    ) -> PostAppointmentWorkflowResponse:
        """Initiate a post-appointment workflow.
        
        Args:
            workflow_request: The workflow request containing appointment ID and options
            person_id: The person whose care record is being updated
            caregiver_id: The caregiver initiating the workflow
            
        Returns:
            PostAppointmentWorkflowResponse with workflow ID and next steps
        """
        # Authorization check: ensure the authenticated user can access the person
        # In a real implementation, we would use the AuthenticatedPrincipal and check permissions
        # For now, we'll assume the caller has already been authorized
        
        # Generate a workflow ID
        workflow_id = uuid.uuid4()
        
        # Store workflow information
        _WORKFLOW_STORAGE[workflow_id] = {
            "appointment_id": workflow_request.appointment_id,
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "status": "initiated",
            "initiated_at": datetime.now(timezone.utc),
            "next_steps": ["open_capture_workspace"]
        }
        
        # For now, we'll return a response indicating the workflow is ready
        # In a full implementation, we would create a workflow record in the database
        
        next_steps = [
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
        
        if workflow_request.capture_immediately:
            # If requested, we could immediately return the capture workspace data
            pass
        
        return PostAppointmentWorkflowResponse(
            workflow_id=str(workflow_id),
            appointment_id=str(workflow_request.appointment_id),
            status="initiated",
            created_at=datetime.now(timezone.utc),
            next_steps=next_steps
        )
    
    def capture_appointment_data(
        self, 
        workflow_id: uuid.UUID,
        capture_data: PostAppointmentCaptureData
    ) -> Dict[str, Any]:
        """Capture appointment outcome and related information.
        
        Args:
            workflow_id: The workflow identifier
            capture_data: The data captured from the appointment
            
        Returns:
            Dictionary with capture results and next steps
        """
        # Retrieve workflow information from storage
        workflow_info = _WORKFLOW_STORAGE.get(workflow_id)
        if not workflow_info:
            raise ValueError(f"Workflow {workflow_id} not found or expired")
        
        appointment_id = workflow_info["appointment_id"]
        person_id = workflow_info["person_id"]
        
        # Get the appointment to verify it exists
        appointment = self.db.query(AppointmentModel).filter(
            AppointmentModel.id == str(appointment_id)
        ).first()
        
        if not appointment:
            raise ValueError(f"Appointment {appointment_id} not found")
        
        # Convert capture data to evidence if appropriate
        evidence_entries = []
        
        # If we have a professional response or document, create evidence
        if capture_data.capture_source in ["professional_response", "professional_document", "caregiver_report"]:
            evidence_entry = EvidenceModel(
                person_id=person_id,
                source_text=capture_data.model_dump_json(),  # Store the captured data as JSON
                uploaded_at=capture_data.capture_timestamp,
                uploaded_by_caregiver_id=str(capture_data.captured_by_caregiver_id),
                source_type="professional" if capture_data.capture_source in ["professional_response", "professional_document"] else "caregiver",
                evidence_status="recorded",
                confidence=capture_data.appointment_outcome_confidence or 0.8,
                extra_metadata=json.dumps({
                    "workflow_id": str(workflow_id),
                    "capture_type": "post_appointment",
                    "source": capture_data.capture_source,
                    "appointment_id": str(appointment_id)
                })
            )
            self.db.add(evidence_entry)
            self.db.flush()
            evidence_entries.append(evidence_entry)
        
        # Create a care event for the appointment completion/update
        care_event = CareEvent(
            person_id=person_id,
            event_type="appointment_update",
            status="recorded",
            occurred_at=capture_data.capture_timestamp,
            title=f"Appointment Update: {appointment_id}",
            description=f"Post-appointment workflow capture for appointment {appointment_id}",
            evidence_ids=[str(e.id) for e in evidence_entries] if evidence_entries else [],
            created_by_caregiver_id=str(capture_data.captured_by_caregiver_id),
            reporter_ids=[str(capture_data.captured_by_caregiver_id)],
            time_provenance=capture_data.capture_timestamp.strftime("%Y-%m-%d %H:%M:%S %Z"),
            confidence=capture_data.appointment_outcome_confidence or 0.8
        )
        self.db.add(care_event)
        self.db.flush()
        
        # Link the evidence to the care event if we created evidence
        if evidence_entries:
            for evidence in evidence_entries:
                evidence.extra_metadata = json.dumps({
                    **json.loads(evidence.extra_metadata or "{}"),
                    "linked_care_event_id": str(care_event.id)
                })
        
        # Update workflow status
        workflow_info["status"] = "captured"
        workflow_info["captured_at"] = datetime.now(timezone.utc)
        workflow_info["capture_data"] = capture_data
        workflow_info["care_event_id"] = str(care_event.id)
        workflow_info["evidence_ids"] = [str(e.id) for e in evidence_entries]
        
        self.db.commit()
        
        return {
            "capture_id": uuid.uuid4(),
            "workflow_id": workflow_id,
            "appointment_id": str(appointment_id),
            "care_event_id": str(care_event.id),
            "status": "captured",
            "evidence_created": len(evidence_entries),
            "next_steps": ["reconcile_information"]
        }
    
    def reconcile_information(
        self,
        workflow_id: uuid.UUID,
        capture_data: PostAppointmentCaptureData,
        person_id: str
    ) -> PostAppointmentReconciliationResult:
        """Reconcile new appointment information with existing care state.
        
        Args:
            workflow_id: The workflow identifier
            capture_data: The captured appointment data
            person_id: The person whose care record is being updated
            
        Returns:
            PostAppointmentReconciliationResult with reconciliation details
        """
        # Retrieve workflow information from storage
        workflow_info = _WORKFLOW_STORAGE.get(workflow_id)
        if not workflow_info:
            raise ValueError(f"Workflow {workflow_id} not found or expired")
        
        # Verify the person_id matches the workflow
        if workflow_info["person_id"] != person_id:
            raise ValueError(f"Person ID mismatch: workflow is for {workflow_info['person_id']}, but {person_id} was provided")
        
        # Get the current care state for this person from existing care events and evidence
        # We'll look at recent care events to understand the current state
        recent_events = self.db.query(CareEvent).filter(
            CareEvent.person_id == person_id
        ).order_by(
            CareEvent.occurred_at.desc()
        ).limit(50).all()
        
        # Get recent evidence
        recent_evidence = self.db.query(EvidenceModel).filter(
            EvidenceModel.person_id == person_id
        ).order_by(
            EvidenceModel.uploaded_at.desc()
        ).limit(100).all()
        
        # Build a representation of current state
        current_state = {
            "events": [self._event_to_dict(e) for e in recent_events],
            "evidence": [self._evidence_to_dict(e) for e in recent_evidence],
            "medications": self._extract_medication_info(recent_events, recent_evidence),
            "conditions": self._extract_condition_info(recent_events, recent_evidence),
            "appointments": self._extract_appointment_info(recent_events)
        }
        
        # Extract new information from capture data
        new_info = self._extract_capture_data_info(capture_data)
        
        # Perform reconciliation by comparing current state with new information
        unchanged_items = []
        updated_items = []
        superseded_items = []
        confirmed_items = []
        contradicted_items = []
        unresolved_items = []
        unknown_items = []
        
        # Reconcile medications
        med_reconciliation = self._reconcile_medications(
            current_state["medications"], 
            new_info.get("medications", [])
        )
        unchanged_items.extend(med_reconciliation["unchanged"])
        updated_items.extend(med_reconciliation["updated"])
        superseded_items.extend(med_reconciliation["superseded"])
        confirmed_items.extend(med_reconciliation["confirmed"])
        
        # Reconcile conditions/diagnoses
        condition_reconciliation = self._reconcile_conditions(
            current_state["conditions"], 
            new_info.get("conditions", [])
        )
        unchanged_items.extend(condition_reconciliation["unchanged"])
        updated_items.extend(condition_reconciliation["updated"])
        superseded_items.extend(condition_reconciliation["superseded"])
        confirmed_items.extend(condition_reconciliation["confirmed"])
        contradicted_items.extend(condition_reconciliation["contradicted"])
        
        # Reconcile appointments
        appt_reconciliation = self._reconcile_appointments(
            current_state["appointments"], 
            new_info.get("appointments", [])
        )
        unchanged_items.extend(appt_reconciliation["unchanged"])
        updated_items.extend(appt_reconciliation["updated"])
        superseded_items.extend(appt_reconciliation["superseded"])
        confirmed_items.extend(appt_reconciliation["confirmed"])
        
        # Mark any unresolved items from the capture data that couldn't be reconciled
        # This would be more sophisticated in a real implementation
        if new_info.get("unresolved_questions"):
            unresolved_items.extend(new_info["unresolved_questions"])
        
        # Update workflow status
        workflow_info["status"] = "reconciled"
        workflow_info["reconciliation_result"] = {
            "unchanged_count": len(unchanged_items),
            "updated_count": len(updated_items),
            "superseded_count": len(superseded_items),
            "confirmed_count": len(confirmed_items),
            "contradicted_count": len(contradicted_items),
            "unresolved_count": len(unresolved_items)
        }
        
        self.db.commit()
        
        return PostAppointmentReconciliationResult(
            reconciliation_id=uuid.uuid4(),
            unchanged_items=unchanged_items,
            updated_items=updated_items,
            superseded_items=superseded_items,
            confirmed_items=confirmed_items,
            contradicted_items=contradicted_items,
            unresolved_items=unresolved_items,
            unknown_items=unknown_items
        )
    
    def update_care_state(
        self,
        workflow_id: uuid.UUID,
        reconciliation_result: PostAppointmentReconciliationResult,
        person_id: str
    ) -> PostAppointmentCareStateUpdate:
        """Update the canonical care state based on reconciliation results.
        
        Args:
            workflow_id: The workflow identifier
            reconciliation_result: The result of information reconciliation
            person_id: The person whose care record is being updated
            
        Returns:
            PostAppointmentCareStateUpdate with details of the care state update
        """
        # Retrieve workflow information from storage
        workflow_info = _WORKFLOW_STORAGE.get(workflow_id)
        if not workflow_info:
            raise ValueError(f"Workflow {workflow_id} not found or expired")
        
        # Verify the person_id matches the workflow
        if workflow_info["person_id"] != person_id:
            raise ValueError(f"Person ID mismatch: workflow is for {workflow_info['person_id']}, but {person_id} was provided")
        
        # Get the care event and evidence from the capture step
        care_event_id = workflow_info.get("care_event_id")
        evidence_ids = workflow_info.get("evidence_ids", [])
        
        # Track what we've updated
        updated_fields = []
        previous_state_summary = {}
        new_state_summary = {}
        
        # If we have a care event from the capture, update it with reconciled information
        if care_event_id:
            care_event = self.db.query(CareEvent).filter(
                CareEvent.id == care_event_id
            ).first()
            
            if care_event:
                # Update the care event with reconciled status
                previous_state_summary["care_event_status"] = care_event.status
                care_event.status = "recorded"  # or another appropriate status
                updated_fields.append("care_event_status")
                
                # Add reconciliation metadata to the care event
                reconciliation_metadata = {
                    "reconciliation_id": str(reconciliation_result.reconciliation_id),
                    "unchanged_count": len(reconciliation_result.unchanged_items),
                    "updated_count": len(reconciliation_result.updated_items),
                    "superseded_count": len(reconciliation_result.superseded_items),
                    "confirmed_count": len(reconciliation_result.confirmed_items),
                    "contradicted_count": len(reconciliation_result.contradicted_items),
                    "unresolved_count": len(reconciliation_result.unresolved_items)
                }
                
                # Update the care event's extra_metadata with reconciliation info
                existing_metadata = json.loads(care_event.extra_metadata or "{}")
                existing_metadata.update({
                    "post_appointment_workflow": reconciliation_metadata
                })
                care_event.extra_metadata = json.dumps(existing_metadata)
                updated_fields.append("extra_metadata")
        
        # For superseded information, we would typically create new care events 
        # that supersede old ones, or update existing ones with supersession links
        # For now, we'll track what was superseded
        if reconciliation_result.superseded_items:
            previous_state_summary["superseded_items_count"] = len(reconciliation_result.superseded_items)
            new_state_summary["superseded_items_count"] = len(reconciliation_result.superseded_items)
            updated_fields.append("superseded_tracking")
        
        # For confirmed information, we might want to boost confidence or create supporting evidence
        if reconciliation_result.confirmed_items:
            previous_state_summary["confirmed_items_count"] = 0
            new_state_summary["confirmed_items_count"] = len(reconciliation_result.confirmed_items)
            updated_fields.append("confirmed_tracking")
        
        # Update workflow status
        workflow_info["status"] = "care_state_updated"
        workflow_info["care_state_updated_at"] = datetime.now(timezone.utc)
        workflow_info["updated_fields"] = updated_fields
        
        self.db.commit()
        
        # Determine the new care state version (in a real system, this might come from a version table)
        care_state_version = 1  # Simplified
        
        return PostAppointmentCareStateUpdate(
            care_state_version=care_state_version,
            updated_fields=updated_fields,
            previous_state_summary=previous_state_summary,
            new_state_summary=new_state_summary,
            evidence_ids=[uuid.UUID(eid) for eid in evidence_ids] if evidence_ids else []
        )
    
    def update_open_loops(
        self,
        workflow_id: uuid.UUID,
        capture_data: PostAppointmentCaptureData,
        person_id: str
    ) -> Dict[str, Any]:
        """Update open loops based on appointment outcomes.
        
        Args:
            workflow_id: The workflow identifier
            capture_data: The captured appointment data
            person_id: The person whose care record is being updated
            
        Returns:
            Dictionary with open loop update results
        """
        # Retrieve workflow information from storage
        workflow_info = _WORKFLOW_STORAGE.get(workflow_id)
        if not workflow_info:
            raise ValueError(f"Workflow {workflow_id} not found or expired")
        
        # Verify the person_id matches the workflow
        if workflow_info["person_id"] != person_id:
            raise ValueError(f"Person ID mismatch: workflow is for {workflow_info['person_id']}, but {person_id} was provided")
        
        # Import the follow-up engine
        from app.services.follow_up import FollowUpEngine
        follow_up_engine = FollowUpEngine(self.db)
        
        # Get existing open/scheduled follow-ups for this person
        existing_followups = follow_up_engine.list_for_person(
            person_id=person_id,
            status="scheduled"  # Assuming we have a way to check status
        )
        
        # If we don't have direct access to status enum, let's get all and filter
        all_followups = follow_up_engine.list_for_person(person_id=person_id)
        existing_followups = [f for f in all_followups if getattr(f, 'status', None) and str(f.status) == 'scheduled']
        
        loops_resolved = 0
        loops_created = 0
        loops_updated = 0
        
        # Check if any existing follow-ups were resolved by the appointment
        # This would involve matching follow-up triggers with appointment outcomes
        for followup in existing_followups:
            # Simple heuristic: if we have medication changes and the followup was for medication review, resolve it
            if capture_data.medication_changes and followup.trigger:
                trigger_str = str(followup.trigger).lower()
                if "medication" in trigger_str or "med" in trigger_str:
                    # Mark as resolved/completed
                    try:
                        followup_engine.complete(
                            followup_id=str(followup.id),
                            outcome=f"Resolved by appointment outcomes: {len(capture_data.medication_changes)} medication changes documented"
                        )
                        loops_resolved += 1
                    except Exception as e:
                        logger.warning(f"Failed to complete followup {followup.id}: {e}")
            
            # If we have new diagnosis and the followup was for condition monitoring, resolve it
            if capture_data.new_diagnosis and followup.trigger:
                trigger_str = str(followup.trigger).lower()
                if any(term in trigger_str for term in ["condition", "diagnosis", "illness"]):
                    # Mark as resolved/completed
                    try:
                        followup_engine.complete(
                            followup_id=str(followup.id),
                            outcome=f"Resolved by appointment: new diagnosis '{capture_data.new_diagnosis}' documented"
                        )
                        loops_resolved += 1
                    except Exception as e:
                        logger.warning(f"Failed to complete followup {followup.id}: {e}")
        
        # Create new follow-ups based on appointment outcomes that require follow-up
        # New appointments scheduled
        if capture_data.new_appointments:
            for appt in capture_data.new_appointments:
                if appt.get("type") == "follow_up" or appt.get("purpose") == "follow_up":
                    try:
                        # Parse the scheduled time if provided
                        due_at = None
                        if appt.get("scheduled_at"):
                            try:
                                due_at = datetime.fromisoformat(appt["scheduled_at"].replace("Z", "+00:00"))
                            except:
                                due_at = datetime.now(timezone.utc) + timedelta(days=7)  # Default to 1 week
                        else:
                            due_at = datetime.now(timezone.utc) + timedelta(days=7)
                        
                        followup_engine.schedule(
                            what_to_recheck=f"Follow-up appointment: {appt.get('title', 'Unknown follow-up')}",
                            why_open=f"Follow-up appointment scheduled during post-appointment workflow",
                            what_result_changes_the_plan="Results from the follow-up appointment may change the care plan",
                            trigger="planned_review",  # Using a reasonable default
                            due_at=due_at,
                            person_id=person_id,
                            caregiver_id=workflow_info["caregiver_id"]
                        )
                        loops_created += 1
                    except Exception as e:
                        logger.warning(f"Failed to schedule followup for appointment {appt}: {e}")
        
        # New tests/orders that require follow-up for results
        if capture_data.new_tests_orders:
            for test_order in capture_data.new_tests_orders:
                if test_order.get("requires_follow_up", False) or "result" in test_order.get("type", "").lower():
                    try:
                        # Schedule follow-up for when results should be available
                        due_at = datetime.now(timezone.utc) + timedelta(days=14)  # Default 2 weeks for test results
                        if test_order.get("expected_result_date"):
                            try:
                                due_at = datetime.fromisoformat(test_order["expected_result_date"].replace("Z", "+00:00"))
                            except:
                                pass  # Use default
                        
                        followup_engine.schedule(
                            what_to_recheck=f"Test results: {test_order.get('type', 'Unknown test')}",
                            why_open=f"Test ordered during appointment requires follow-up for results",
                            what_result_changes_the_plan="Test results may significantly change diagnosis and treatment plan",
                            trigger="planned_review",
                            due_at=due_at,
                            person_id=person_id,
                            caregiver_id=workflow_info["caregiver_id"]
                        )
                        loops_created += 1
                    except Exception as e:
                        logger.warning(f"Failed to schedule followup for test {test_order}: {e}")
        
        # Responsibility-based follow-ups
        if capture_data.new_responsibilities:
            for resp in capture_data.new_responsibilities:
                if resp.get("requires_follow_up", False) or resp.get("type") in ["medication_administration", "procedure_aftercare"]:
                    try:
                        due_at = datetime.now(timezone.utc) + timedelta(days=3)  # Quick follow-up for new responsibilities
                        if resp.get("follow_up_date"):
                            try:
                                due_at = datetime.fromisoformat(resp["follow_up_date"].replace("Z", "+00:00"))
                            except:
                                pass  # Use default
                        
                        followup_engine.schedule(
                            what_to_recheck=f"Responsibility adherence: {resp.get('description', 'New responsibility')}",
                            why_open=f"New responsibility assigned during appointment requires adherence follow-up",
                            what_result_changes_the_plan="Adherence to new responsibility may affect health outcomes",
                            trigger="uncertainty",  # Using uncertainty trigger for new responsibilities
                            due_at=due_at,
                            person_id=person_id,
                            caregiver_id=workflow_info["caregiver_id"]
                        )
                        loops_created += 1
                    except Exception as e:
                        logger.warning(f"Failed to schedule followup for responsibility {resp}: {e}")
        
        # Update workflow status
        workflow_info["status"] = "open_loops_updated"
        workflow_info["open_loops_updated_at"] = datetime.now(timezone.utc)
        workflow_info["loops_resolved"] = loops_resolved
        workflow_info["loops_created"] = loops_created
        workflow_info["loops_updated"] = loops_updated
        
        self.db.commit()
        
        return {
            "workflow_id": str(workflow_id),
            "loops_resolved": loops_resolved,
            "loops_created": loops_created,
            "loops_updated": loops_updated,
            "status": "open_loops_updated",
            "next_steps": ["update_responsibilities"]
        }
    
    def update_responsibilities(
        self,
        workflow_id: uuid.UUID,
        capture_data: PostAppointmentCaptureData,
        person_id: str
    ) -> Dict[str, Any]:
        """Update responsibility assignments based on appointment outcomes.
        
        Args:
            workflow_id: The workflow identifier
            capture_data: The captured appointment data
            person_id: The person whose care record is being updated
            
        Returns:
            Dictionary with responsibility update results
        """
        # Retrieve workflow information from storage
        workflow_info = _WORKFLOW_STORAGE.get(workflow_id)
        if not workflow_info:
            raise ValueError(f"Workflow {workflow_id} not found or expired")
        
        # Verify the person_id matches the workflow
        if workflow_info["person_id"] != person_id:
            raise ValueError(f"Person ID mismatch: workflow is for {workflow_info['person_id']}, but {person_id} was provided")
        
        # Import the caregiver intelligence service
        caregiver_intelligence_service = CaregiverIntelligenceService(self.db)
        
        responsibilities_created = 0
        responsibilities_updated = 0
        responsibilities_resolved = 0
        
        # Process new responsibilities from capture data
        if capture_data.new_responsibilities:
            for resp_data in capture_data.new_responsibilities:
                try:
                    # Extract responsibility information
                    responsibility_type = resp_data.get("type", "general")
                    description = resp_data.get("description", "")
                    assignee_info = resp_data.get("assignee", {})
                    schedule_info = resp_data.get("schedule", {})
                    
                    # Determine the caregiver ID - in a real implementation, this would come from
                    # the assignee information or caregiver-patient relationship
                    # For now, we'll use the workflow caregiver
                    caregiver_id = workflow_info["caregiver_id"]
                    
                    # Check if assignee information specifies a different caregiver
                    if assignee_info.get("caregiver_id"):
                        try:
                            caregiver_id = uuid.UUID(assignee_info["caregiver_id"])
                        except:
                            # If we can't parse it, stick with the workflow caregiver
                            pass
                    elif assignee_info.get("name"):
                        # In a real implementation, we'd look up the caregiver by name
                        # For now, we'll stick with the workflow caregiver
                        pass
                    
                    # Create the responsibility
                    responsibility_create = CaregiverResponsibilityCreate(
                        caregiver_id=caregiver_id,
                        person_id=person_id,
                        responsibility_type=responsibility_type,
                        scope=description,
                        effective_start=datetime.now(timezone.utc),
                        effective_end=None,  # Ongoing unless specified otherwise
                        status=ResponsibilityStatus.ACTIVE,
                        acceptance_status=resp_data.get("acceptance_status", "explicit") if "acceptance_status" in resp_data else "explicit",
                        assignment_source="post_appointment_workflow",
                        is_joint=False,
                        schedule_pattern=schedule_info.get("pattern") if schedule_info else None
                    )
                    
                    # Assign the responsibility
                    responsibility = caregiver_intelligence_service.assign_responsibility(responsibility_create)
                    responsibilities_created += 1
                    
                    # If there's a specific end date, update it
                    if resp_data.get("effective_end"):
                        try:
                            end_date = datetime.fromisoformat(resp_data["effective_end"].replace("Z", "+00:00"))
                            responsibility.effective_end = end_date
                            self.db.add(responsibility)
                            self.db.flush()
                        except:
                            pass  # Keep as ongoing if we can't parse the date
                
                except Exception as e:
                    logger.warning(f"Failed to create responsibility from data {resp_data}: {e}")
        
        # Update existing responsibilities based on appointment outcomes
        # For example, if a responsibility was completed or changed
        existing_responsibilities = caregiver_intelligence_service.get_active_responsibilities(person_id=person_id)
        
        for resp in existing_responsibilities:
            # Check if appointment outcomes suggest this responsibility should be updated
            updated = False
            
            # If we have medication changes and this is a medication responsibility, check for changes
            if capture_data.medication_changes and "medication" in resp.responsibility_type.lower():
                # In a real implementation, we'd compare specific medications
                # For now, we'll assume medication responsibilities might need updating
                updated = True
            
            # If we have new diagnosis and this is a condition monitoring responsibility
            if capture_data.new_diagnosis and any(term in resp.responsibility_type.lower() for term in ["condition", "monitor"]):
                updated = True
            
            if updated:
                try:
                    # Update the responsibility (in a real implementation, we might create a new version)
                    # For now, we'll just note it as updated
                    responsibilities_updated += 1
                except Exception as e:
                    logger.warning(f"Failed to update responsibility {resp.id}: {e}")
            
            # Check if responsibility should be marked as completed/resolved
            resolved = False
            if resp_data.get("completed") or (capture_data.appointment_outcome == "completed" and "follow_up" in resp.responsibility_type.lower()):
                resolved = True
            
            if resolved:
                try:
                    # In a real implementation, we might update status to historical or completed
                    # For now, we'll just count it
                    responsibilities_resolved += 1
                except Exception as e:
                    logger.warning(f"Failed to mark responsibility {resp.id} as resolved: {e}")
        
        # Update workflow status
        workflow_info["status"] = "responsibilities_updated"
        workflow_info["responsibilities_updated_at"] = datetime.now(timezone.utc)
        workflow_info["responsibilities_created"] = responsibilities_created
        workflow_info["responsibilities_updated"] = responsibilities_updated
        workflow_info["responsibilities_resolved"] = responsibilities_resolved
        
        self.db.commit()
        
        return {
            "workflow_id": str(workflow_id),
            "responsibilities_created": responsibilities_created,
            "responsibilities_updated": responsibilities_updated,
            "responsibilities_resolved": responsibilities_resolved,
            "status": "responsibilities_updated",
            "next_steps": ["update_upcoming_events"]
        }
    
    def update_upcoming_events(
        self,
        workflow_id: uuid.UUID,
        capture_data: PostAppointmentCaptureData,
        person_id: str
    ) -> Dict[str, Any]:
        """Update upcoming events based on appointment outcomes.
        
        Args:
            workflow_id: The workflow identifier
            capture_data: The captured appointment data
            person_id: The person whose care record is being updated
            
        Returns:
            Dictionary with upcoming events update results
        """
        # Retrieve workflow information from storage
        workflow_info = _WORKFLOW_STORAGE.get(workflow_id)
        if not workflow_info:
            raise ValueError(f"Workflow {workflow_id} not found or expired")
        
        # Verify the person_id matches the workflow
        if workflow_info["person_id"] != person_id:
            raise ValueError(f"Person ID mismatch: workflow is for {workflow_info['person_id']}, but {person_id} was provided")
        
        events_created = 0
        events_updated = 0
        events_completed = 0
        
        # Create new appointments from capture data
        if capture_data.new_appointments:
            for appt_data in capture_data.new_appointments:
                try:
                    # Skip if this is marked as a follow-up (we handle those separately via follow-up engine)
                    if appt_data.get("type") == "follow_up":
                        continue
                    
                    # Parse the scheduled time
                    scheduled_at = None
                    if appt_data.get("scheduled_at"):
                        try:
                            scheduled_at = datetime.fromisoformat(appt_data["scheduled_at"].replace("Z", "+00:00"))
                        except:
                            # If we can't parse the time, skip this appointment
                            logger.warning(f"Could not parse scheduled time for appointment: {appt_data}")
                            continue
                    else:
                        # If no time provided, we can't create the appointment
                        logger.warning(f"No scheduled time provided for appointment: {appt_data}")
                        continue
                    
                    # Create the appointment
                    appointment = AppointmentModel(
                        person_id=person_id,
                        title=appt_data.get("title", "Follow-up Appointment"),
                        scheduled_at=scheduled_at,
                        scheduled_at_timezone=appt_data.get("timezone", "UTC"),
                        location=appt_data.get("location"),
                        location_type=appt_data.get("location_type"),
                        provider=appt_data.get("provider"),
                        healthcare_org=appt_data.get("healthcare_org"),
                        preparation_notes=appt_data.get("preparation_notes"),
                        documents_to_bring=appt_data.get("documents_to_bring", []),
                        medication_questions=appt_data.get("medication_questions", []),
                        travel_notes=appt_data.get("travel_notes"),
                        status="scheduled",
                        time_provenance=scheduled_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
                        location_provenance=appt_data.get("location_provenance"),
                        evidence_ids=[],  # Will be populated if there's supporting evidence
                        created_by_caregiver_id=str(workflow_info["caregiver_id"])
                    )
                    
                    self.db.add(appointment)
                    self.db.flush()
                    events_created += 1
                    
                    # If there's evidence supporting this appointment, create evidence records
                    if appt_data.get("supporting_evidence"):
                        for evidence_data in appt_data["supporting_evidence"]:
                            try:
                                evidence = EvidenceModel(
                                    person_id=person_id,
                                    type=evidence_data.get("type", "appointment_scheduling"),
                                    source_text=evidence_data.get("source_text", ""),
                                    uploaded_at=datetime.now(timezone.utc),
                                    uploaded_by_caregiver_id=str(workflow_info["caregiver_id"]),
                                    source_type="professional",
                                    evidence_status="recorded",
                                    confidence=evidence_data.get("confidence", 0.8),
                                    extra_metadata=json.dumps({
                                        "source": "post_appointment_workflow",
                                        "appointment_id": str(appointment.id),
                                        "evidence_type": evidence_data.get("type")
                                    })
                                )
                                self.db.add(evidence)
                                self.db.flush()
                                
                                # Link evidence to appointment
                                # In a real implementation, we might update the appointment's evidence_ids
                                # For now, we'll just note the connection
                            except Exception as e:
                                logger.warning(f"Failed to create evidence for appointment {appointment.id}: {e}")
                
                except Exception as e:
                    logger.warning(f"Failed to create appointment from data {appt_data}: {e}")
        
        # Mark existing appointments as completed if the appointment outcome indicates completion
        # This would typically be handled by the appointment lifecycle service, but we can note it here
        if capture_data.appointment_outcome == "completed":
            # Find the original appointment that triggered this workflow
            original_appointment_id = workflow_info["appointment_id"]
            original_appointment = self.db.query(AppointmentModel).filter(
                AppointmentModel.id == original_appointment_id
            ).first()
            
            if original_appointment and original_appointment.status != "completed":
                # Note: In a real implementation, the appointment lifecycle would be updated
                # through the proper channels, but we can track that it should be completed
                events_completed += 1
                # We don't actually update the appointment here to avoid interfering with
                # the normal appointment lifecycle management
        
        # Create care events for upcoming tests/orders that need to be tracked
        if capture_data.new_tests_orders:
            for test_order in capture_data.new_tests_orders:
                if test_order.get("type") not in ["follow_up", "appointment"]:  # Avoid duplicates
                    try:
                        # Schedule the care event for when the test should occur
                        occurred_at = None
                        if test_order.get("scheduled_date"):
                            try:
                                occurred_at = datetime.fromisoformat(test_order["scheduled_date"].replace("Z", "+00:00"))
                            except:
                                occurred_at = datetime.now(timezone.utc) + timedelta(days=7)  # Default
                        else:
                            occurred_at = datetime.now(timezone.utc) + timedelta(days=7)
                        
                        care_event = CareEvent(
                            person_id=person_id,
                            event_type=test_order.get("type", "test_order"),
                            status="scheduled",
                            occurred_at=occurred_at,
                            title=test_order.get("title", f"Test Order: {test_order.get('type', 'Unknown')}"),
                            description=test_order.get("description", ""),
                            created_by_caregiver_id=str(workflow_info["caregiver_id"]),
                             reporter_ids=[str(workflow_info["caregiver_id"])],
                            time_provenance=occurred_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
                            confidence=test_order.get("confidence", 0.7)
                        )
                        self.db.add(care_event)
                        self.db.flush()
                        events_created += 1
                        
                    except Exception as e:
                        logger.warning(f"Failed to create care event for test order {test_order}: {e}")
        
        # Update workflow status
        workflow_info["status"] = "upcoming_events_updated"
        workflow_info["upcoming_events_updated_at"] = datetime.now(timezone.utc)
        workflow_info["events_created"] = events_created
        workflow_info["events_updated"] = events_updated
        workflow_info["events_completed"] = events_completed
        
        self.db.commit()
        
        return {
            "workflow_id": str(workflow_id),
            "events_created": events_created,
            "events_updated": events_updated,
            "events_completed": events_completed,
            "status": "upcoming_events_updated",
            "next_steps": ["update_handoff_context"]
        }
    
    def update_handoff_context(
        self,
        workflow_id: uuid.UUID,
        person_id: str
    ) -> Dict[str, Any]:
        """Update caregiver handoff context after appointment.
        
        Args:
            workflow_id: The workflow identifier
            person_id: The person whose care record is being updated
            
        Returns:
            Dictionary with handoff context update results
        """
        # Retrieve workflow information from storage
        workflow_info = _WORKFLOW_STORAGE.get(workflow_id)
        if not workflow_info:
            raise ValueError(f"Workflow {workflow_id} not found or expired")
        
        # Verify the person_id matches the workflow
        if workflow_info["person_id"] != person_id:
            raise ValueError(f"Person ID mismatch: workflow is for {workflow_info['person_id']}, but {person_id} was provided")
        
        # Import handoff-related models and services
        from app.models.handoff import Handoff, HandoffResponsibility
        from app.core.handoff_enums import HandoffStatus
        
        handoff_context_updated = False
        handoff_version = 1
        
        try:
            # Check if there's an existing handoff for this person that needs updating
            # In a real implementation, we'd query for active handoffs
            # For now, we'll either create a new one or update an existing one
            
            # Look for recent handoffs for this person
            existing_handoffs = self.db.query(Handoff).filter(
                Handoff.person_id == person_id
            ).order_by(
                Handoff.created_at.desc()
            ).limit(5).all()
            
            handoff_to_update = None
            if existing_handoffs:
                # Take the most recent handoff that's not historical/archived
                for handoff in existing_handoffs:
                     handoff_lifecycle = getattr(handoff, 'lifecycle_state', None)
                     if handoff_lifecycle and str(handoff_lifecycle) not in ['historical', 'archived', 'revoked']:
                        handoff_to_update = handoff
                        break
            
            if handoff_to_update:
                # Update existing handoff
                handoff_to_update.updated_at = datetime.now(timezone.utc)
                handoff_context_updated = True
                handoff_version = getattr(handoff_to_update, 'version', 1) + 1
                handoff_to_update.version = handoff_version
                
                # Update handoff content with latest information from the workflow
                # In a real implementation, we'd update specific sections of the handoff
                handoff_to_update.notes = f"""Updated via post-appointment workflow {workflow_id}:
- Care state updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}
- Workflow status: {workflow_info.get('status', 'unknown')}
- Appointment processed: {workflow_info.get('appointment_id')}

This handoff has been updated to reflect the current care state following the appointment."""  
                
            else:
                # Create a new handoff
                handoff = Handoff(
                    person_id=person_id,
                    title=f"Post-Appointment Handoff - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                    description=f"Handoff context updated following post-appointment workflow for appointment {workflow_info.get('appointment_id')}",
                    handoff_type="post_appointment_update",
                     lifecycle_state=HandoffLifecycleState.ACTIVE,
                    version=1,
                    created_by_caregiver_id=str(workflow_info["caregiver_id"]),
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                    notes=f"""Initial handoff created via post-appointment workflow {workflow_id}:
- Based on appointment: {workflow_info.get('appointment_id')}
- Created: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}
- Workflow status: {workflow_info.get('status', 'unknown')}

This handoff contains the current care state following the appointment and should be used for care coordination."""  
                )
                
                self.db.add(handoff)
                self.db.flush()
                handoff_context_updated = True
                handoff_version = 1
                
                # Create handoff responsibilities based on current responsibilities
                try:
                    from app.services.caregiver_intelligence import CaregiverIntelligenceService
                    caregiver_intelligence_service = CaregiverIntelligenceService(self.db)
                    active_responsibilities = caregiver_intelligence_service.get_active_responsibilities(person_id=person_id)
                    
                    for resp in active_responsibilities[:10]:  # Limit to prevent too many handoff items
                        handoff_responsibility = HandoffResponsibility(
                            handoff_id=handoff.id,
                            responsibility_type=resp.responsibility_type,
                             status=getattr(ResponsibilityStatus, "PROPOSED", "proposed"),  # Default status
                            description=resp.scope or f"Responsibility: {resp.responsibility_type}",
                            assigned_to_caregiver_id=resp.caregiver_id,
                            effective_start=resp.effective_start,
                            effective_end=resp.effective_end
                        )
                        self.db.add(handoff_responsibility)
                except Exception as e:
                    logger.warning(f"Failed to create handoff responsibilities: {e}")
                
        except Exception as e:
            logger.error(f"Failed to update handoff context: {e}")
            # Don't fail the entire workflow for handoff issues
            handoff_context_updated = False
        
        # Update workflow status
        workflow_info["status"] = "handoff_context_updated"
        workflow_info["handoff_context_updated_at"] = datetime.now(timezone.utc)
        workflow_info["handoff_context_updated"] = handoff_context_updated
        workflow_info["handoff_version"] = handoff_version
        
        self.db.commit()
        
        return {
            "workflow_id": str(workflow_id),
            "handoff_context_updated": handoff_context_updated,
            "handoff_version": handoff_version,
            "status": "handoff_context_updated",
            "next_steps": ["generate_family_summary_if_appropriate"]
        }
    
    def generate_family_summary(
        self,
        workflow_id: uuid.UUID,
        person_id: str,
        viewer_caregiver_id: Optional[uuid.UUID] = None
    ) -> Optional[Dict[str, Any]]:
        """Generate a family summary if appropriate.
        
        Args:
            workflow_id: The workflow identifier
            person_id: The person whose care record is being updated
            viewer_caregiver_id: The caregiver requesting the summary (for authorization)
            
        Returns:
            Dictionary with family summary data, or None if not appropriate
        """
        # Retrieve workflow information from storage
        workflow_info = _WORKFLOW_STORAGE.get(workflow_id)
        if not workflow_info:
            raise ValueError(f"Workflow {workflow_id} not found or expired")
        
        # Verify the person_id matches the workflow
        if workflow_info["person_id"] != person_id:
            raise ValueError(f"Person ID mismatch: workflow is for {workflow_info['person_id']}, but {person_id} was provided")
        
        # Check if we have sufficient information to generate a summary
        # For example, if we have updated the care state or have new information
        if workflow_info.get("status") not in ["care_state_updated", "handoff_context_updated", "responsibilities_updated", "upcoming_events_updated"]:
            return None
        
        # Gather information for the summary
        appointment_id = workflow_info.get("appointment_id")
        status = workflow_info.get("status", "")
        
        # Extract key information from workflow
        medication_changes = []
        new_diagnosis = None
        new_appointments = []
        responsibilities_created = 0
        responsibilities_updated = 0
        
        # If we have capture data, extract information from it
        capture_data = workflow_info.get("capture_data")
        if capture_data:
            if hasattr(capture_data, 'medication_changes') and capture_data.medication_changes:
                medication_changes = capture_data.medication_changes
            if hasattr(capture_data, 'new_diagnosis') and capture_data.new_diagnosis:
                new_diagnosis = capture_data.new_diagnosis
            if hasattr(capture_data, 'new_appointments') and capture_data.new_appointments:
                new_appointments = capture_data.new_appointments
        
        # Extract information from workflow status tracking
        if "medications_updated" in workflow_info:
            medication_changes.extend(workflow_info["medications_updated"])
        if "conditions_updated" in workflow_info:
            # Extract diagnosis information
            pass
        if "responsibilities_created" in workflow_info:
            responsibilities_created = workflow_info["responsibilities_created"]
        if "responsibilities_updated" in workflow_info:
            responsibilities_updated = workflow_info["responsibilities_updated"]
        if "events_created" in workflow_info:
            new_appointments.extend(workflow_info["events_created"])
        
        # Build summary text
        summary_parts = []
        summary_parts.append(f"Following the appointment on {appointment_id or 'the recent visit'},")
        
        if medication_changes or new_diagnosis or responsibilities_created > 0:
            summary_parts.append("the care plan has been updated")
        else:
            summary_parts.append("the care plan remains stable")
            
        summary_parts.append("with the following key changes:")
        
        # Build key points
        key_points = []
        
        if medication_changes:
            med_count = len(medication_changes)
            key_points.append(f"{med_count} medication change{'s' if med_count != 1 else ''} documented")
            
            # Add specific medication changes if available
            for med in medication_changes[:3]:  # Limit to first 3
                if isinstance(med, dict):
                    med_name = med.get("medication", "unknown medication")
                    change_type = med.get("change", "adjusted")
                    key_points.append(f"  • {med_name}: {change_type}")
        
        if new_diagnosis:
            key_points.append(f"New diagnosis: {new_diagnosis}")
        
        if responsibilities_created > 0:
            key_points.append(f"{responsibilities_created} new responsibility{'ies' if responsibilities_created != 1 else ''} assigned")
        
        if responsibilities_updated > 0:
            key_points.append(f"{responsibilities_updated} responsibility{'ies' if responsibilities_updated != 1 else ''} updated")
        
        if new_appointments:
            apt_count = len(new_appointments)
            key_points.append(f"{apt_count} follow-up appointment{'s' if apt_count != 1 else ''} scheduled")
            
            # Add specific appointments if available
            for apt in new_appointments[:3]:  # Limit to first 3
                if isinstance(apt, dict):
                    apt_title = apt.get("title", "Follow-up appointment")
                    apt_date = apt.get("scheduled_at", "date TBD")
                    key_points.append(f"  • {apt_title} ({apt_date})")
        
        # If no specific changes were found, provide a general update
        if not key_points:
            key_points.append("Care plan reviewed and confirmed as current")
            key_points.append("No significant changes detected at this time")
        
        # Build next steps
        next_steps = []
        
        if medication_changes:
            next_steps.append("Review medication changes with pharmacy or nurse")
        
        if new_appointments:
            next_steps.append("Prepare for upcoming appointments")
            next_steps.append("Schedule transportation if needed for appointments")
        
        if responsibilities_created > 0 or responsibilities_updated > 0:
            next_steps.append("Review new or updated responsibilities with care team")
        
        # Add general next steps
        next_steps.append("Monitor for any changes in condition or symptoms")
        next_steps.append("Maintain regular communication with care team")
        
        # Limit lists to reasonable size
        key_points = key_points[:6]
        next_steps = next_steps[:5]
        
        return {
            "summary_id": str(uuid.uuid4()),
            "person_id": person_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": " ".join(summary_parts),
            "key_points": key_points,
            "next_steps": next_steps
        }
    
    # Helper methods
    def _event_to_dict(self, event: CareEvent) -> Dict[str, Any]:
        """Convert a CareEvent model to a dictionary."""
        return {
            "id": str(event.id),
            "event_type": event.event_type,
            "title": event.title,
            "description": event.description,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            "recorded_at": event.recorded_at.isoformat(),
            "status": event.status,
            "confidence": event.confidence,
            "evidence_ids": event.evidence_ids or [],
            "created_by_caregiver_id": event.created_by_caregiver_id
        }
    
    def _evidence_to_dict(self, evidence: EvidenceModel) -> Dict[str, Any]:
        """Convert an Evidence model to a dictionary."""
        return {
            "id": str(evidence.id),
            "type": evidence.type,
            "source_text": evidence.source_text[:100] + "..." if len(evidence.source_text) > 100 else evidence.source_text,
            "uploaded_at": evidence.uploaded_at.isoformat(),
            "uploaded_by_caregiver_id": evidence.uploaded_by_caregiver_id,
            "confidence": evidence.confidence,
            "evidence_status": evidence.evidence_status,
            "source_type": evidence.source_type
        }
    
    def _extract_medication_info(self, events, evidence) -> list:
        """Extract medication information from events and evidence."""
        medications = []
        # In a full implementation, we would parse events and evidence for medication information
        # For now, we'll return an empty list
        return medications
    
    def _extract_condition_info(self, events, evidence) -> list:
        """Extract condition/diagnosis information from events and evidence."""
        conditions = []
        # In a full implementation, we would parse events and evidence for condition information
        # For now, we'll return an empty list
        return conditions
    
    def _extract_appointment_info(self, events) -> list:
        """Extract appointment information from events."""
        appointments = []
        # In a full implementation, we would parse events for appointment information
        # For now, we'll return an empty list
        return appointments
    
    def _extract_capture_data_info(self, capture_data: PostAppointmentCaptureData) -> Dict[str, Any]:
        """Extract structured information from capture data."""
        info = {}
        
        # Extract medication changes
        if capture_data.medication_changes:
            info["medications"] = capture_data.medication_changes
        
        # Extract new diagnosis
        if capture_data.new_diagnosis:
            info["conditions"] = [{"name": capture_data.new_diagnosis, "confidence": capture_data.appointment_outcome_confidence}]
        
        # Extract new appointments
        if capture_data.new_appointments:
            info["appointments"] = capture_data.new_appointments
        
        # Extract unresolved questions
        if hasattr(capture_data, 'unresolved_questions') and capture_data.unresolved_questions:
            info["unresolved_questions"] = capture_data.unresolved_questions
        
        return info
    
    def _reconcile_medications(self, current_medications, new_medications) -> Dict[str, list]:
        """Reconcile medication lists."""
        # Simplified reconciliation - in reality, this would be more complex
        unchanged = []
        updated = []
        superseded = []
        confirmed = []
        
        # For now, we'll treat all new medications as updated/confirmed
        for med in new_medications:
            # Check if it matches any current medication (simplified)
            matched = False
            for curr_med in current_medications:
                if isinstance(curr_med, dict) and isinstance(med, dict):
                    if curr_med.get("medication") == med.get("medication"):
                        matched = True
                        # If the change is the same, it's unchanged; otherwise updated
                        if curr_med.get("change") == med.get("change") and curr_med.get("dosage") == med.get("dosage"):
                            unchanged.append(med)
                        else:
                            updated.append(med)
                        break
                elif curr_med == med:
                    matched = True
                    unchanged.append(med)
                    break
            
            if not matched:
                confirmed.append(med)
        
        return {
            "unchanged": unchanged,
            "updated": updated,
            "superseded": superseded,
            "confirmed": confirmed
        }
    
    def _reconcile_conditions(self, current_conditions, new_conditions) -> Dict[str, list]:
        """Reconcile condition/diagnosis lists."""
        # Simplified reconciliation
        unchanged = []
        updated = []
        superseded = []
        confirmed = []
        contradicted = []
        
        # For now, we'll treat all new conditions as confirmed if not present, or updated if changed
        for cond in new_conditions:
            matched = False
            for curr_cond in current_conditions:
                if isinstance(curr_cond, dict) and isinstance(cond, dict):
                    if curr_cond.get("name") == cond.get("name"):
                        matched = True
                        if curr_cond.get("confidence") == cond.get("confidence"):
                            unchanged.append(cond)
                        else:
                            updated.append(cond)
                        break
                elif curr_cond == cond:
                    matched = True
                    unchanged.append(cond)
                    break
            
            if not matched:
                confirmed.append(cond)
        
        return {
            "unchanged": unchanged,
            "updated": updated,
            "superseded": superseded,
            "confirmed": confirmed,
            "contradicted": contradicted
        }
    
    def _reconcile_appointments(self, current_appointments, new_appointments) -> Dict[str, list]:
        """Reconcile appointment lists."""
        # Simplified reconciliation
        unchanged = []
        updated = []
        superseded = []
        confirmed = []
        
        # For now, we'll treat all new appointments as confirmed if not present, or updated if changed
        for appt in new_appointments:
            matched = False
            for curr_appt in current_appointments:
                if isinstance(curr_appt, dict) and isinstance(appt, dict):
                    if curr_appt.get("title") == appt.get("title") and curr_appt.get("scheduled_at") == appt.get("scheduled_at"):
                        matched = True
                        if curr_appt.get("status") == appt.get("status"):
                            unchanged.append(appt)
                        else:
                            updated.append(appt)
                        break
                elif curr_appt == appt:
                    matched = True
                    unchanged.append(appt)
                    break
            
            if not matched:
                confirmed.append(appt)
        
        return {
            "unchanged": unchanged,
            "updated": updated,
            "superseded": superseded,
            "confirmed": confirmed
        }