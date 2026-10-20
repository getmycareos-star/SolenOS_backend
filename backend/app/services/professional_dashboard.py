"""Professional Dashboard service for SolenOS.

Implements business logic for professional caregiver dashboard.
"""

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc, func
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import logging

from app.models.identity import Account, Person, ProfessionalProfile, ProfessionalRoleLink
from app.models.care_event import CareEvent
from app.models.care import Insight
from app.models.meaning import Meaning
from app.models.behavior import BehaviorObservation, BehaviorInterpretation
from app.models.follow_up import FollowUp
from app.models.action import Action
from app.models.care_circle import CareCircle, CareCircleMembership
from app.schemas.professional_dashboard import (
    ProfessionalDashboard,
    ProfessionalDashboardPersonSummary,
    ProfessionalDashboardAlertItem,
    ProfessionalDashboardAlert
)
from app.core.database import utc_now
from app.services.notification import create_notification_service
from app.schemas.notification import NotificationCreate, NotificationType


class ProfessionalDashboardService:
    """Service for generating professional caregiver dashboards."""
    
    def __init__(self, db: Session):
        self.db = db
        self.logger = logging.getLogger(__name__)
    
    def generate_professional_dashboard(
        self, 
        professional_account_id: str, 
        timeframe_days: int = 7
    ) -> ProfessionalDashboard:
        """Generate a professional dashboard for a professional caregiver."""
        # Get the professional's account and profile
        account = self.db.query(Account).filter(Account.id == professional_account_id).first()
        if not account:
            raise ValueError(f"Account with ID {professional_account_id} not found")
        
        professional_profile = self.db.query(ProfessionalProfile).filter(
            ProfessionalProfile.account_id == professional_account_id
        ).first()
        
        if not professional_profile:
            raise ValueError(f"Professional profile not found for account {professional_account_id}")
        
        # Get the persons this professional is authorized to care for
        professional_roles = self.db.query(ProfessionalRoleLink).join(ProfessionalProfile).filter(
            and_(
                ProfessionalRoleLink.professional_profile_id == professional_profile.id,
                ProfessionalRoleLink.is_active == 1,
                ProfessionalProfile.verification_status.in_([
                    "verified",  # Assuming these are the enum values
                    "VERIFIED"
                ])
            )
        ).all()
        
        person_ids = [role.person_id for role in professional_roles]
        
        if not person_ids:
            # Return empty dashboard if no authorized persons
            return ProfessionalDashboard(
                total_patients=0,
                active_patients_today=0,
                patients_with_alerts=0,
                total_alerts=0,
                alerts=[],
                patients=[],
                professional_name=professional_profile.full_name,
                professional_role=professional_profile.professional_role.value if hasattr(professional_profile.professional_role, 'value') else str(professional_profile.professional_role),
                specialty=professional_profile.specialty,
                generated_at=utc_now(),
                timeframe_days=timeframe_days
            )
        
        # Get the persons
        persons = self.db.query(Person).filter(Person.id.in_(person_ids)).all()
        
        # Calculate cutoff time for recent activity
        cutoff_time = utc_now() - timedelta(days=timeframe_days)
        
        # Generate patient summaries and alerts
        patient_summaries = []
        all_alerts = []
        
        for person in persons:
            patient_summary, alerts = self._generate_person_summary_and_alerts(
                person, professional_profile, cutoff_time
            )
            patient_summaries.append(patient_summary)
            all_alerts.extend(alerts)
        
        # Sort alerts by severity and timestamp (most recent/severe first)
        alert_priority = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        all_alerts.sort(
            key=lambda x: (
                alert_priority.get(x.severity, 0), 
                x.timestamp
            ), 
            reverse=True
        )
        
        # Calculate summary statistics
        total_patients = len(persons)
        active_patients_today = len([
            p for p in patient_summaries 
            if p.last_updated >= (utc_now() - timedelta(hours=24))
        ])
        patients_with_alerts = len(set([alert.person_id for alert in all_alerts]))
        total_alerts = len(all_alerts)
        
        return ProfessionalDashboard(
            total_patients=total_patients,
            active_patients_today=active_patients_today,
            patients_with_alerts=patients_with_alerts,
            total_alerts=total_alerts,
            alerts=all_alerts[:50],  # Limit to top 50 alerts
            patients=patient_summaries,
            professional_name=professional_profile.full_name,
            professional_role=professional_profile.professional_role.value if hasattr(professional_profile.professional_role, 'value') else str(professional_profile.professional_role),
            specialty=professional_profile.specialty,
            generated_at=utc_now(),
            timeframe_days=timeframe_days
        )
    
    def _generate_person_summary_and_alerts(
        self, 
        person: Person, 
        professional_profile: ProfessionalProfile,
        cutoff_time: datetime
    ) -> tuple[ProfessionalDashboardPersonSummary, List[ProfessionalDashboardAlertItem]]:
        """Generate a summary and alerts for a specific person."""
        alerts = []
        
        # Get recent care events
        recent_events = self.db.query(CareEvent).filter(
            and_(
                CareEvent.person_id == person.id,
                CareEvent.occurred_at >= cutoff_time if CareEvent.occurred_at.is_not(None) else True,
                CareEvent.is_active == 1
            )
        ).order_by(desc(CareEvent.occurred_at)).limit(10).all()
        
        # Get recent insights
        recent_insights = self.db.query(Insight).filter(
            and_(
                Insight.person_id == person.id,
                Insight.created_at >= cutoff_time,
                Insight.is_active == 1
            )
        ).order_by(desc(Insight.created_at)).limit(5).all()
        
        # Get recent meaning updates
        recent_meanings = self.db.query(Meaning).filter(
            and_(
                Meaning.person_id == person.id,
                Meaning.created_at >= cutoff_time
            )
        ).order_by(desc(Meaning.created_at)).limit(5).all()
        
        # Get recent behavior observations
        recent_observations = self.db.query(BehaviorObservation).filter(
            and_(
                BehaviorObservation.person_id == person.id,
                BehaviorObservation.occurred_at >= cutoff_time if BehaviorObservation.occurred_at.is_not(None) else True
            )
        ).order_by(desc(BehaviorObservation.occurred_at)).limit(10).all()
        
        # Get recent follow-ups
        upcoming_followups = self.db.query(FollowUp).filter(
            and_(
                FollowUp.person_id == person.id,
                FollowUp.due_at >= utc_now(),
                FollowUp.status == "scheduled",
                FollowUp.is_active == 1
            )
        ).order_by(FollowUp.due_at).limit(5).all()
        
        # Get recent actions
        recent_actions = self.db.query(Action).filter(
            and_(
                Action.person_id == person.id,
                Action.created_at >= cutoff_time,
                Action.is_active == 1
            )
        ).order_by(desc(Action.created_at)).limit(10).all()
        
        # Check for care circle updates
        care_circles = self.db.query(CareCircle).join(CareCircleMembership).filter(
            and_(
                CareCircle.person_id == person.id,
                CareCircle.is_active == 1,
                CareCircleMembership.is_active == 1,
                or_(
                    CareCircleMembership.member_account_id == professional_profile.account_id,
                    CareCircleMembership.member_person_id == None  # Would need to check via person's account
                )
            )
        ).all()
        
        # Generate alerts based on recent activity
        # New symptoms or worsening conditions from care events
        for event in recent_events:
            if event.event_type in ["symptom_report", "condition_update", "assessment"]:
                # Check if this indicates a new or worsening symptom
                if any(keyword in (event.title or "").lower() for keyword in ["new", "worse", "increased", "severe"]):
                    alerts.append(ProfessionalDashboardAlertItem(
                        person_id=person.id,
                        person_name=person.name,
                        alert_type=ProfessionalDashboardAlert.NEW_SYMPTOM if "new" in (event.title or "").lower() else ProfessionalDashboardAlert.SYMPTOM_WORSENING,
                        title=f"New symptom reported: {event.title or 'Unknown'}",
                        description=event.description or "No description provided",
                        severity="medium",
                        timestamp=event.occurred_at or event.recorded_at or utc_now(),
                        action_required=True,
                        suggested_action="Review the symptom report and consider if clinical intervention is needed"
                    ))
                elif any(keyword in (event.title or "").lower() for keyword in ["fall", "injury", "accident"]):
                    alerts.append(ProfessionalDashboardAlertItem(
                        person_id=person.id,
                        person_name=person.name,
                        alert_type=ProfessionalDashboardAlert.NEW_SYMPTOM,
                        title=f"Potential injury: {event.title or 'Unknown event'}",
                        description=event.description or "No description provided",
                        severity="high",
                        timestamp=event.occurred_at or event.recorded_at or utc_now(),
                        action_required=True,
                        suggested_action="Assess for injury and follow up with appropriate medical care"
                    ))
        
        # Check for medication-related events
        for event in recent_events:
            if event.event_type == "medication":
                alerts.append(ProfessionalDashboardAlertItem(
                    person_id=person.id,
                    person_name=person.name,
                    alert_type=ProfessionalDashboardAlert.MEDICATION_CHANGE,
                    title=f"Medication update: {event.title or 'Medication change'}",
                    description=event.description or "No description provided",
                    severity="medium",
                    timestamp=event.occurred_at or event.recorded_at or utc_now(),
                    action_required=False,
                    suggested_action="Review the medication change for appropriateness"
                ))
        
        # Check for missed appointments (follow-ups past due)
        missed_followups = self.db.query(FollowUp).filter(
            and_(
                FollowUp.person_id == person.id,
                FollowUp.due_at < utc_now(),
                FollowUp.status == "scheduled",
                FollowUp.is_active == 1
            )
        ).limit(5).all()
        
        for followup in missed_followups:
            alerts.append(ProfessionalDashboardAlertItem(
                person_id=person.id,
                person_name=person.name,
                alert_type=ProfessionalDashboardAlert.APPOINTMENT_MISSED,
                title=f"Missed follow-up: {followup.what_to_recheck or 'Unknown follow-up'}",
                description=followup.why_open or "No reason provided",
                severity="medium",
                timestamp=followup.due_at or utc_now(),
                action_required=True,
                suggested_action="Contact the patient/reschedule the follow-up"
            ))
        
        # Check for upcoming appointments
        for followup in upcoming_followups:
            if followup.due_at <= utc_now() + timedelta(days=1):  # Within next 24 hours
                alerts.append(ProfessionalDashboardAlertItem(
                    person_id=person.id,
                    person_name=person.name,
                    alert_type=ProfessionalDashboardAlert.APPOINTMENT_UPCOMING,
                    title=f"Upcoming follow-up: {followup.what_to_recheck or 'Unknown follow-up'}",
                    description=followup.why_open or "No reason provided",
                    severity="low",
                    timestamp=followup.due_at or utc_now(),
                    action_required=False,
                    suggested_action="Prepare for the upcoming follow-up"
                ))
        
        # Check for concerning behavior observations
        for observation in recent_observations:
            if observation.is_safety_concern:
                alerts.append(ProfessionalDashboardAlertItem(
                    person_id=person.id,
                    person_name=person.name,
                    alert_type=ProfessionalDashboardAlert.BEHAVIOR_CHANGE,
                    title=f"Safety concern in behavior: {observation.category.value if hasattr(observation.category, 'value') else str(observation.category)}",
                    description=observation.description or observation.original_text or "No description",
                    severity="high",
                    timestamp=observation.occurred_at or utc_now(),
                    action_required=True,
                    suggested_action="Review the behavior observation and implement safety measures if needed"
                ))
            elif observation.category and str(observation.category).lower() in ["cognition", "memory", "orientation"]:
                alerts.append(ProfessionalDashboardAlertItem(
                    person_id=person.id,
                    person_name=person.name,
                    alert_type=ProfessionalDashboardAlert.COGNITIVE_CHANGE,
                    title=f"Cognitive change observed: {observation.description or 'Change in cognitive function'}",
                    description=observation.original_text or "No original text",
                    severity="medium",
                    timestamp=observation.occurred_at or utc_now(),
                    action_required=False,
                    suggested_action="Monitor cognitive changes and consider formal assessment if persistent"
                ))
        
        # Check for concerning insights
        for insight in recent_insights:
            if insight.insight_type in ["risk_assessment", "prognosis"] and insight.confidence and insight.confidence > 0.7:
                alerts.append(ProfessionalDashboardAlertItem(
                    person_id=person.id,
                    person_name=person.name,
                    alert_type=ProfessionalDashboardAlert.SYMPTOM_WORSENING,
                    title=f"Clinical insight: {insight.title}",
                    description=insight.description or "No description",
                    severity="medium" if insight.confidence < 0.85 else "high",
                    timestamp=insight.created_at or utc_now(),
                    action_required=False,
                    suggested_action="Review the clinical insight and consider implications for care plan"
                ))
        
        # Calculate person summary
        # Determine last updated time from various sources
        last_updated_times = []
        if recent_events:
            last_updated_times.extend([e.occurred_at or e.recorded_at for e in recent_events if e.occurred_at or e.recorded_at])
        if recent_insights:
            last_updated_times.extend([i.created_at for i in recent_insights])
        if recent_meanings:
            last_updated_times.extend([m.created_at for m in recent_meanings])
        if recent_observations:
            last_updated_times.extend([o.occurred_at for o in recent_observations if o.occurred_at])
        if recent_actions:
            last_updated_times.extend([a.created_at for a in recent_actions])
        if care_circles:
            last_updated_times.extend([cc.updated_at for cc in care_circles])
        
        last_updated = max(last_updated_times) if last_updated_times else utc_now()
        
        # Determine if there are recent updates
        has_recent_updates = len(last_updated_times) > 0
        
        # Get next appointment
        next_appointment = None
        if upcoming_followups:
            next_appointment = min([f.due_at for f in upcoming_followups if f.due_at])
        
        # Calculate simple compliance score (placeholder)
        # In a real implementation, this would be based on adherence to care plans, medication compliance, etc.
        compliance_score = None
        if recent_events or recent_insights or recent_actions:
            # Simple heuristic: more recent activity = higher compliance
            activity_count = len(recent_events) + len(recent_insights) + len(recent_actions)
            compliance_score = min(100.0, activity_count * 10)  # Cap at 100
        
        # Get care team size (count of caregivers and professionals linked to this person)
        # This would require querying the account_person_links and professional_role_links
        # For simplicity, we'll use a placeholder
        care_team_size = len([cc for cc in care_circles if cc.is_active])  # Approximate
        
        person_summary = ProfessionalDashboardPersonSummary(
            person_id=person.id,
            person_name=person.name,
            primary_condition=person.primary_condition,
            last_updated=last_updated or utc_now(),
            alert_count=len([a for a in alerts if a.person_id == person.id]),
            alerts=[a.alert_type for a in alerts if a.person_id == person.id],
            care_team_size=care_team_size,
            has_recent_updates=has_recent_updates,
            next_appointment=next_appointment,
            compliance_score=compliance_score
        )
        
        return person_summary, alerts