"""API endpoints for medication management and Medication Day view."""

from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.identity import Person
from app.services.medication import MedicationService
from app.schemas.medication import (
    MedicationCreate,
    MedicationUpdate,
    MedicationResponse,
    MedicationListResponse,
    MedicationReconciliationResult,
    MedicationReconciliationComparison
)

router = APIRouter(prefix="/medication", tags=["medication"])


def get_medication_service(db: Session = Depends(get_db)) -> MedicationService:
    return MedicationService(db)


def get_person_or_404(person_id: str, db: Session = Depends(get_db)) -> Person:
    person = db.query(Person).filter(Person.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


@router.post("/{person_id}", response_model=MedicationResponse)
def create_medication(
    person_id: str = Path(..., description="Person ID"),
    medication_data: MedicationCreate = None,
    service: MedicationService = Depends(get_medication_service),
    person: Person = Depends(get_person_or_404)
):
    """Create a new medication record for a person."""
    # Ensure person_id matches
    medication_data.person_id = person_id
    medication = service.create_medication(medication_data)
    return medication


@router.get("/{person_id}", response_model=MedicationListResponse)
def list_medications(
    person_id: str = Path(..., description="Person ID"),
    active_only: bool = Query(False, description="Return only active medications"),
    skip: int = Query(0, description="Number of records to skip"),
    limit: int = Query(100, description="Maximum number of records to return"),
    service: MedicationService = Depends(get_medication_service),
    person: Person = Depends(get_person_or_404)
):
    """List medications for a person."""
    medications = service.get_person_medications(person_id, active_only=active_only)
    total = len(medications)
    
    # Apply pagination
    paginated_medications = medications[skip:skip + limit]
    
    return MedicationListResponse(
        medications=paginated_medications,
        total=total,
        page=skip // limit + 1 if limit > 0 else 1,
        size=len(paginated_medications)
    )


@router.get("/{person_id}/{medication_id}", response_model=MedicationResponse)
def get_medication(
    person_id: str = Path(..., description="Person ID"),
    medication_id: str = Path(..., description="Medication ID"),
    service: MedicationService = Depends(get_medication_service),
    person: Person = Depends(get_person_or_404)
):
    """Get a specific medication by ID."""
    medication = service.get_medication(medication_id)
    if not medication:
        raise HTTPException(status_code=404, detail="Medication not found")
    
    # Verify medication belongs to person
    if medication.person_id != person_id:
        raise HTTPException(status_code=403, detail="Medication does not belong to this person")
        
    return medication


@router.put("/{person_id}/{medication_id}", response_model=MedicationResponse)
def update_medication(
    person_id: str = Path(..., description="Person ID"),
    medication_id: str = Path(..., description="Medication ID"),
    medication_data: MedicationUpdate = None,
    service: MedicationService = Depends(get_medication_service),
    person: Person = Depends(get_person_or_404)
):
    """Update a medication record."""
    medication = service.get_medication(medication_id)
    if not medication:
        raise HTTPException(status_code=404, detail="Medication not found")
    
    # Verify medication belongs to person
    if medication.person_id != person_id:
        raise HTTPException(status_code=403, detail="Medication does not belong to this person")
        
    updated_medication = service.update_medication(medication_id, medication_data)
    if not updated_medication:
        raise HTTPException(status_code=404, detail="Medication not found")
        
    return updated_medication


@router.delete("/{person_id}/{medication_id}")
def deprecate_medication(
    person_id: str = Path(..., description="Person ID"),
    medication_id: str = Path(..., description="Medication ID"),
    reason: Optional[str] = Query(None, description="Reason for deprecation"),
    service: MedicationService = Depends(get_medication_service),
    person: Person = Depends(get_person_or_404)
):
    """Deprecate a medication (mark as historical)."""
    medication = service.get_medication(medication_id)
    if not medication:
        raise HTTPException(status_code=404, detail="Medication not found")
    
    # Verify medication belongs to person
    if medication.person_id != person_id:
        raise HTTPException(status_code=403, detail="Medication does not belong to this person")
        
    deprecated_medication = service.deprecate_medication(medication_id, reason)
    if not deprecated_medication:
        raise HTTPException(status_code=404, detail="Medication not found")
        
    return {"message": "Medication deprecated successfully"}


@router.post("/{person_id}/reconcile", response_model=MedicationReconciliationResult)
def reconcile_medications(
    person_id: str = Path(..., description="Person ID"),
    comparison_type: str = Query(..., description="Type of comparison (e.g., pre_hospital_vs_post_hospital)"),
    # In a real implementation, these would come from request body
    # For now, we'll accept medication lists as query parameters for simplicity
    # In practice, this should be in the request body as JSON
    list_a_ids: Optional[List[str]] = Query(None, description="Medication IDs for first list"),
    list_b_ids: Optional[List[str]] = Query(None, description="Medication IDs for second list"),
    service: MedicationService = Depends(get_medication_service),
    person: Person = Depends(get_person_or_404)
):
    """Reconcile medication lists (e.g., pre-hospital vs post-hospital)."""
    # Get medication service to fetch medications by ID
    if list_a_ids is not None and list_b_ids is not None:
        # Fetch specific medications by ID
        list_a = []
        list_b = []
        
        for med_id in list_a_ids:
            medication = service.get_medication(med_id)
            if medication and medication.person_id == person_id:
                list_a.append(medication)
        
        for med_id in list_b_ids:
            medication = service.get_medication(med_id)
            if medication and medication.person_id == person_id:
                list_b.append(medication)
    else:
        # Fallback: get all medications and split reasonably (not arbitrarily)
        # This is only for backward compatibility/demo purposes
        current_medications = service.get_person_medications(person_id, active_only=False)
        
        # Split by active vs inactive status instead of arbitrarily
        active_meds = [m for m in current_medications if getattr(m, 'is_active', True) or (getattr(m, 'end_date', None) is None)]
        inactive_meds = [m for m in current_medications if not (getattr(m, 'is_active', True) or (getattr(m, 'end_date', None) is None))]
        
        list_a = active_meds
        list_b = inactive_meds if inactive_meds else active_meds[:len(active_meds)//2] if active_meds else []
        
        # Ensure we have two lists to compare
        if not list_b:
            list_b = list_a.copy() if list_a else []
    
    # Only proceed if we have meaningful lists to compare
    if not list_a and not list_b:
        # Return empty reconciliation result
        return MedicationReconciliationResult(
            comparison_type=comparison_type,
            comparisons=[],
            summary={
                "total_matches": 0,
                "total_conflicts": 0,
                "total_only_in_a": 0,
                "total_only_in_b": 0,
                "note": "No medications provided for comparison"
            }
        )
    
    result = service.reconcile_medication_lists(person_id, list_a, list_b, comparison_type)
    return result


@router.get("/{person_id}/medication-day", response_model=dict)
def get_medication_day_view(
    person_id: str = Path(..., description="Person ID"),
    service: MedicationService = Depends(get_medication_service),
    person: Person = Depends(get_person_or_404)
):
    """Get the Medication Day view for a person."""
    return service.get_medication_day_view(person_id)


@router.post("/{person_id}/version", response_model=MedicationResponse)
def create_medication_version(
    person_id: str = Path(..., description="Person ID"),
    previous_medication_id: str = Query(..., description="ID of previous medication version"),
    change_type: str = Query(..., description="Type of change (dose, frequency, etc.)"),
    change_source: str = Query(..., description="Source of the change"),
    # In practice, these would be in request body
    change_data: dict = None,
    service: MedicationService = Depends(get_medication_service),
    person: Person = Depends(get_person_or_404)
):
    """Create a new version of a medication with changes."""
    # Verify person_id matches
    previous_medication = service.get_medication(previous_medication_id)
    if not previous_medication:
        raise HTTPException(status_code=404, detail="Previous medication not found")
        
    if previous_medication.person_id != person_id:
        raise HTTPException(status_code=403, detail="Medication does not belong to this person")
        
    # Parse change_data (in practice, this would come from request body)
    changes = change_data or {}
    
    new_medication = service.create_medication_version(
        previous_medication_id=previous_medication_id,
        changes=changes,
        change_type=change_type,
        change_source=change_source
    )
    
    return new_medication