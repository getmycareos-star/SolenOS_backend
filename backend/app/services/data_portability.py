import json
import datetime
from datetime import datetime
import csv
import io
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from app.models.care_event import CareEvent
from app.models.evidence import Evidence
from app.models.identity import Person
from app.models.continuity import ContinuityHandoff
from app.models.care import CarePlan
from app.models.follow_up import FollowUp

class DataPortabilityService:
    def __init__(self, db: Session):
        self.db = db
    
    def export_person_data(self, person_id: str) -> Dict[str, Any]:
        \"\"\"Export all data for a person as JSON\"\"\"
        # Verify person exists
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f\"Person not found: {person_id}\")
        
        # Export all related data
        data = {
            \"person\": {
                \"id\": person.id,
                \"name\": getattr(person, 'name', None),
                \"created_at\": getattr(person, 'created_at', None).isoformat() if getattr(person, 'created_at', None) else None,
                # Add other person fields as needed
            },
            \"care_events\": [
                {
                    \"id\": event.id,
                    \"event_type\": event.event_type,
                    \"title\": event.title,
                    \"description\": event.description,
                    \"occurred_at\": event.occurred_at.isoformat() if event.occurred_at else None,
                    \"tags\": event.tags or [],
                    \"created_at\": event.created_at.isoformat() if event.created_at else None
                }
                for event in self.db.query(CareEvent)
                .filter(CareEvent.person_id == person_id)
                .order_by(CareEvent.occurred_at.desc())
                .all()
            ],
            \"evidence\": [
                {
                    \"id\": evidence.id,
                    \"type\": evidence.type,
                    \"source_text\": evidence.source_text,
                    \"confidence\": getattr(evidence, 'confidence', None),
                    \"uploaded_at\": evidence.uploaded_at.isoformat() if evidence.uploaded_at else None,
                    \"created_at\": evidence.created_at.isoformat() if evidence.created_at else None
                }
                for evidence in self.db.query(Evidence)
                .filter(Evidence.person_id == person_id)
                .order_by(Evidence.uploaded_at.desc())
                .all()
            ],
            \"care_plans\": [
                {
                    \"id\": plan.id,
                    \"title\": plan.title,
                    \"description\": plan.description,
                    \"start_date\": plan.start_date.isoformat() if plan.start_date else None,
                    \"end_date\": plan.end_date.isoformat() if plan.end_date else None,
                    \"created_at\": plan.created_at.isoformat() if plan.created_at else None
                }
                for plan in self.db.query(CarePlan)
                .filter(CarePlan.person_id == person_id)
                .order_by(CarePlan.start_date.desc())
                .all()
            ],
            \"follow_ups\": [
                {
                    \"id\": followup.id,
                    \"title\": followup.title,
                    \"description\": followup.description,
                    \"due_at\": followup.due_at.isoformat() if followup.due_at else None,
                    \"completed_at\": followup.completed_at.isoformat() if followup.completed_at else None,
                    \"status\": getattr(followup, 'status', None),
                    \"created_at\": followup.created_at.isoformat() if followup.created_at else None
                }
                for followup in self.db.query(FollowUp)
                .filter(FollowUp.person_id == person_id)
                .order_by(FollowUp.due_at.asc())
                .all()
            ],
            \"handoffs\": [
                {
                    \"id\": handoff.id,
                    \"reason\": handoff.reason,
                    \"from_caregiver_id\": handoff.from_caregiver_id,
                    \"to_caregiver_id\": handoff.to_caregiver_id,
                    \"created_at\": handoff.created_at.isoformat() if handoff.created_at else None
                }
                for handoff in self.db.query(ContinuityHandoff)
                .filter(ContinuityHandoff.person_id == person_id)
                .order_by(ContinuityHandoff.created_at.desc())
                .all()
            ],
            \"export_metadata\": {
                \"exported_at\": datetime.utcnow().isoformat(),
                \"exported_by\": \"system\",  # In real implementation, get from auth context
                \"format_version\": \"1.0\"
            }
        }
        return data
    
    def export_to_csv(self, person_id: str, table_name: str) -> str:
        \"\"\"Export specific table data as CSV\"\"\"
        output = io.StringIO()
        writer = csv.writer(output)
        
        if table_name.lower() == \"care_events\":
            writer.writerow([
                \"ID\", \"Event Type\", \"Title\", \"Description\", 
                \"Occurred At\", \"Tags\", \"Created At\"
            ])
            for event in self.db.query(CareEvent).filter(CareEvent.person_id == person_id).order_by(CareEvent.occurred_at.desc()):
                writer.writerow([
                    event.id,
                    event.event_type,
                    event.title,
                    event.description or '',
                    event.occurred_at.isoformat() if event.occurred_at else '',
                    ';'.join(event.tags or []),
                    event.created_at.isoformat() if event.created_at else ''
                ])
        
        elif table_name.lower() == \"evidence\":
            writer.writerow([
                \"ID\", \"Type\", \"Source Text\", \"Confidence\", 
                \"Uploaded At\", \"Created At\"
            ])
            for evidence in self.db.query(Evidence).filter(Evidence.person_id == person_id).order_by(Evidence.uploaded_at.desc()):
                writer.writerow([
                    evidence.id,
                    evidence.type or '',
                    evidence.source_text or '',
                    str(getattr(evidence, 'confidence', '')) if getattr(evidence, 'confidence', None) is not None else '',
                    evidence.uploaded_at.isoformat() if evidence.uploaded_at else '',
                    evidence.created_at.isoformat() if evidence.created_at else ''
                ])
        
        # Add other tables as needed...
        else:
            raise ValueError(f\"Unsupported table for CSV export: {table_name}\")
        
        return output.getvalue()
    
    def import_person_data(self, person_id: str, data: Dict[str, Any]) -> bool:
        \"\"\"Import person data from JSON (creates new records, doesn't update existing)\"\"\"
        # This is a simplified implementation - in production you'd want more robust handling
        # including conflict resolution, validation, etc.
        try:
            # Import care events
            for event_data in data.get(\"care_events\", []):
                event = CareEvent(
                    person_id=person_id,
                    event_type=event_data.get(\"event_type\"),
                    title=event_data.get(\"title\"),
                    description=event_data.get(\"description\"),
                    occurred_at=datetime.fromisoformat(event_data[\"occurred_at\"]) if event_data.get(\"occurred_at\") else None,
                    tags=event_data.get(\"tags\")
                )
                self.db.add(event)
            
            # Import evidence
            for evidence_data in data.get(\"evidence\", []):
                evidence = Evidence(
                    person_id=person_id,
                    type=evidence_data.get(\"type\"),
                    source_text=evidence_data.get(\"source_text\"),
                    confidence=evidence_data.get(\"confidence\"),
                    uploaded_at=datetime.fromisoformat(evidence_data[\"uploaded_at\"]) if evidence_data.get(\"uploaded_at\") else None
                )
                self.db.add(evidence)
            
            # In a real implementation, you would also import other entities
            # and handle relationships properly
            
            self.db.commit()
            return True
            
        except Exception as e:
            self.db.rollback()
            raise e

# Global instance would be created per-request in actual usage
\"\"\"Note: In actual usage, you would create DataPortabilityService instances 
    per request with the request's database session, not use a global instance.\"\"\"
