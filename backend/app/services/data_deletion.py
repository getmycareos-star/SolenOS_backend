"""Automated Data Deletion Service for SolenOS.

Implements automated data deletion based on retention policies defined in the
data classification system. Handles soft deletion, hard deletion, and audit
logging for compliance with data retention requirements.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Type
from sqlalchemy.orm import Session
from sqlalchemy import inspect
from app.core.config import get_settings
from app.core.data_classification import (
    get_retention_period, 
    DataClassification, 
    get_classification,
    get_policy
)
from app.services.alerting import send_system_alert
from app.models.safety import SafetyAuditRecordModel

logger = logging.getLogger(__name__)


class DataDeletionService:
    """Service for automated data deletion based on retention policies."""

    def __init__(self, db_session: Session):
        """Initialize the data deletion service.
        
        Args:
            db_session: SQLAlchemy database session
        """
        self.db = db_session
        self.settings = get_settings()
        self.deleted_count = 0
        self.errors = []

    def delete_expired_data(self, dry_run: bool = False) -> Dict[str, Any]:
        """Delete data that has exceeded its retention period.
        
        Args:
            dry_run: If True, only report what would be deleted without actually deleting
            
        Returns:
            Dictionary with deletion statistics and any errors
        """
        logger.info(f"Starting data deletion process (dry_run={dry_run})")
        
        # Reset counters
        self.deleted_count = 0
        self.errors = []
        
        # Get all tables with classified data
        from app.core.data_classification import DATA_INVENTORY
        
        # Group tables by their base table name for efficient querying
        table_classifications = {}
        for field_path, classification in DATA_INVENTORY.items():
            # Extract table name from field path (e.g., "caregiver_profiles.email" -> "caregiver_profiles")
            table_name = field_path.split('.')[0]
            if table_name not in table_classifications:
                table_classifications[table_name] = []
            table_classifications[table_name].append((field_path, classification))
        
        # Process each table
        for table_name, field_classifications in table_classifications.items():
            try:
                self._process_table(table_name, field_classifications, dry_run)
            except Exception as e:
                error_msg = f"Error processing table {table_name}: {str(e)}"
                logger.error(error_msg)
                self.errors.append(error_msg)
        
        # Commit changes if not dry run
        if not dry_run and self.deleted_count > 0:
            try:
                self.db.commit()
                logger.info(f"Committed {self.deleted_count} record deletions")
            except Exception as e:
                self.db.rollback()
                error_msg = f"Failed to commit deletions: {str(e)}"
                logger.error(error_msg)
                self.errors.append(error_msg)
        elif not dry_run:
            logger.info("No records to delete")
        
        # Send alert if there were errors or significant deletions
        if self.errors or (not dry_run and self.deleted_count > 100):
            send_system_alert(
                component="data_deletion_service",
                description=f"Data deletion completed: {self.deleted_count} records processed, {len(self.errors)} errors",
                severity="warning" if self.errors else "info",
                details={
                    "deleted_count": self.deleted_count,
                    "dry_run": dry_run,
                    "errors": self.errors[:5] if self.errors else []  # Limit error details
                }
            )
        
        return {
            "deleted_count": self.deleted_count,
            "dry_run": dry_run,
            "errors": self.errors,
            "tables_processed": list(table_classifications.keys())
        }

    def _process_table(self, table_name: str, field_classifications: List[tuple], dry_run: bool):
        """Process a single table for data deletion.
        
        Args:
            table_name: Name of the table to process
            field_classifications: List of (field_path, classification) tuples for this table
            dry_run: If True, only report what would be deleted
        """
        # Get the SQLAlchemy model class for this table
        try:
            # Import models dynamically to avoid circular imports
            model_class = self._get_model_class(table_name)
            if not model_class:
                logger.warning(f"Could not find model class for table {table_name}")
                return
        except Exception as e:
            logger.warning(f"Error getting model class for table {table_name}: {str(e)}")
            return
        
        # Determine the most restrictive retention policy among the fields
        # For simplicity, we'll use the shortest retention period among classified fields
        # In a more sophisticated implementation, we might want to consider individual field retention
        min_retention = None
        for _, classification in field_classifications:
            retention = get_retention_period(classification)
            if min_retention is None or retention < min_retention:
                min_retention = retention
        
        if min_retention is None:
            # No classified fields, skip
            return
        
        # Calculate cutoff date
        cutoff_date = datetime.now() - min_retention
        
        # Look for timestamp fields to determine record age
        timestamp_fields = self._get_timestamp_fields(model_class)
        if not timestamp_fields:
            logger.warning(f"No timestamp fields found for table {table_name}, skipping deletion")
            return
        
        # Build query to find expired records
        # We'll use the oldest timestamp field to determine age
        oldest_timestamp_field = timestamp_fields[0]  # Use first timestamp field found
        
        # Query for records older than cutoff
        query = self.db.query(model_class).filter(
            getattr(model_class, oldest_timestamp_field) < cutoff_date
        )
        
        # For safety, limit the number of records we process at once
        # In production, you might want to process in smaller batches
        expired_records = query.limit(1000).all()  # Process up to 1000 records at a time
        
        if not expired_records:
            logger.debug(f"No expired records found in table {table_name}")
            return
        
        logger.info(f"Found {len(expired_records)} expired records in table {table_name}")
        
        # Process each expired record
        for record in expired_records:
            try:
                if dry_run:
                    logger.debug(f"[DRY RUN] Would delete record {getattr(record, 'id', 'unknown')} from {table_name}")
                    self.deleted_count += 1
                else:
                    self._delete_record(record, model_class, table_name, dry_run)
                    self.deleted_count += 1
                    
                    # Create audit record for deletion
                    self._create_deletion_audit_record(
                        record_id=str(getattr(record, 'id', 'unknown')),
                        table_name=table_name,
                        retention_period=min_retention,
                        cutoff_date=cutoff_date
                    )
            except Exception as e:
                error_msg = f"Error deleting record {getattr(record, 'id', 'unknown')} from {table_name}: {str(e)}"
                logger.error(error_msg)
                self.errors.append(error_msg)
    
    def _get_model_class(self, table_name: str) -> Optional[Type]:
        """Get the SQLAlchemy model class for a given table name.
        
        Args:
            table_name: Name of the table
            
        Returns:
            Model class or None if not found
        """
        # Map common table names to their model imports
        # This is a simplified mapping - in practice, you might want to scan all models
        model_mapping = {
            'caregiver_profiles': 'app.models.caregiver',
            'persons': 'app.models.identity',
            'evidence': 'app.models.evidence',
            'care_events': 'app.models.care_event',
            'documents': 'app.models.document',
            'care_plans': 'app.models.care_planning',
            'follow_ups': 'app.models.follow_up',
            'care_share_grants': 'app.models.care_sharing',
            'person_attributes': 'app.models.person_context',
            'person_triggers': 'app.models.person_context',
            'person_calmers': 'app.models.person_context',
            'person_profiles': 'app.models.person_context',
            'audit_logs': 'app.models.safety',  # Assuming audit logs are in safety model
            'sessions': 'app.models.identity',  # Assuming sessions are in identity model
            'llm_reasoning_results': 'app.models.llm',
            'embeddings': 'app.models.llm',
            'caregiver_observations': 'app.models.caregiver_intelligence',
            'caregiver_perspectives': 'app.models.caregiver_intelligence',
            'caregiver_communications': 'app.models.caregiver_intelligence',
            'caregiver_handoffs': 'app.models.caregiver_intelligence',
            'community_posts': 'app.models.community',
            'care_share_access_log': 'app.models.care_sharing',
            'care_share_disclosures': 'app.models.care_sharing',
        }
        
        if table_name not in model_mapping:
            logger.warning(f"No model mapping found for table {table_name}")
            return None
        
        try:
            module_path = model_mapping[table_name]
            module = __import__(module_path, fromlist=['*'])
            
            # Try to find the model class - usually matches table name but singularized
            # This is a heuristic and might need adjustment
            class_name = ''.join(word.capitalize() for word in table_name.split('_'))
            if hasattr(module, class_name):
                return getattr(module, class_name)
            
            # Alternative: look for common model name patterns
            alternative_names = [
                class_name,
                class_name[:-1] if class_name.endswith('s') else class_name,  # Remove trailing s
                class_name + 'Model',
                class_name.capitalize() + 'Model'
            ]
            
            for alt_name in alternative_names:
                if hasattr(module, alt_name):
                    return getattr(module, alt_name)
            
            logger.warning(f"Could not find model class for table {table_name} in module {module_path}")
            return None
        except Exception as e:
            logger.warning(f"Error importing model for table {table_name}: {str(e)}")
            return None
    
    def _get_timestamp_fields(self, model_class: Type) -> List[str]:
        """Get timestamp fields from a model class.
        
        Args:
            model_class: SQLAlchemy model class
            
        Returns:
            List of field names that store timestamps
        """
        timestamp_fields = []
        try:
            # Inspect the model to get column information
            inspector = inspect(model_class)
            for column in inspector.columns:
                # Check if column type is DateTime or similar
                col_type = str(column.type).lower()
                if 'datetime' in col_type or 'timestamp' in col_type or 'date' in col_type:
                    # Exclude fields that are clearly not creation/update timestamps
                    field_name = column.name
                    if not any(exclude in field_name.lower() for exclude in ['birth', 'dob', 'born']):
                        timestamp_fields.append(field_name)
        except Exception as e:
            logger.warning(f"Error inspecting model {model_class}: {str(e)}")
        
        return timestamp_fields
    
    def _delete_record(self, record: Any, model_class: Type, table_name: str, dry_run: bool):
        """Delete a single record.
        
        Args:
            record: The record to delete
            model_class: The model class of the record
            table_name: Name of the table
            dry_run: If True, only simulate deletion
        """
        policy = get_policy(get_classification(f"{table_name}.id"))  # Get policy for table
        
        if policy.hard_delete_allowed:
            # Hard delete - remove from database completely
            if not dry_run:
                self.db.delete(record)
            logger.debug(f"Hard deleted record {getattr(record, 'id', 'unknown')} from {table_name}")
        else:
            # Soft delete - mark as deleted if the model supports it
            # Check if model has a deleted flag or similar field
            if hasattr(record, 'deleted_at'):
                if not dry_run:
                    record.deleted_at = datetime.now()
                logger.debug(f"Soft deleted record {getattr(record, 'id', 'unknown')} from {table_name} (set deleted_at)")
            elif hasattr(record, 'is_deleted'):
                if not dry_run:
                    record.is_deleted = True
                logger.debug(f"Soft deleted record {getattr(record, 'id', 'unknown')} from {table_name} (set is_deleted=True)")
            elif hasattr(record, 'status'):
                # Try to set status to deleted/inactive if possible
                if not dry_run:
                    record.status = 'deleted'  # or 'inactive' depending on system
                logger.debug(f"Soft deleted record {getattr(record, 'id', 'unknown')} from {table_name} (set status='deleted')")
            else:
                # If no soft delete capability, we cannot delete due to policy
                logger.warning(f"Cannot delete record {getattr(record, 'id', 'unknown')} from {table_name}: hard delete not allowed and no soft delete fields found")
                # Don't increment deleted count since we didn't actually delete
                self.deleted_count -= 1  # Compensate for the increment in caller
    
    def _create_deletion_audit_record(self, record_id: str, table_name: str, 
                                    retention_period: timedelta, cutoff_date: datetime):
        """Create an audit record for a data deletion.
        
        Args:
            record_id: ID of the deleted record
            table_name: Name of the table the record was from
            retention_period: The retention period that was applied
            cutoff_date: The cutoff date used for deletion
        """
        try:
            audit_record = SafetyAuditRecordModel(
                id=self._generate_uuid(),
                person_id="system",  # System-initiated deletion
                request_id=self._generate_uuid(),
                user_role="system",
                input_text=f"Automated deletion of record {record_id} from table {table_name}",
                evidence_references=[],
                epistemic_status={"type": "system_operation"},
                contradictions=[],
                safety_decision="ALLOW",  # Deletion is allowed by policy
                safety_state="GROUNDED",
                qualifications=[],
                violations=[],
                policy_references=[f"retention_policy:{table_name}"],
                rationale=f"Record exceeded retention period of {retention_period.days} days",
                safety_version="1.0",
                timestamp=datetime.now(),
                reviewer_id="system",
                override_reason=f"Automated retention enforcement: record older than {cutoff_date}"
            )
            
            # For tamper-proofing, we would normally compute content hash and link to previous record
            # For simplicity in this initial implementation, we'll leave those fields to be handled
            # by the model's default behavior or a separate tamper-proofing mechanism
            
            self.db.add(audit_record)
            # Don't commit here - let the main transaction handle it
        except Exception as e:
            logger.error(f"Failed to create deletion audit record: {str(e)}")
            # Don't raise - we don't want audit failures to prevent data deletion
    
    def _generate_uuid(self) -> str:
        """Generate a UUID string.
        
        Returns:
            UUID as string
        """
        return str(uuid.uuid4())


# Convenience function for external use
def delete_expired_data(db_session: Session, dry_run: bool = False) -> Dict[str, Any]:
    """Convenience function to delete expired data.
    
    Args:
        db_session: SQLAlchemy database session
        dry_run: If True, only report what would be deleted without actually deleting
        
    Returns:
        Dictionary with deletion statistics and any errors
    """
    service = DataDeletionService(db_session)
    return service.delete_expired_data(dry_run=dry_run)


def delete_expired_data_with_db(db_session: Session, dry_run: bool = False) -> Dict[str, Any]:
    """Backward-compatible explicit-session entry point."""
    return delete_expired_data(db_session, dry_run=dry_run)
