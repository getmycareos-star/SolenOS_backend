"""Event Recurrence Grouping Service.

Detects and manages recurrence patterns in care events.
Groups recurring events into meaningful patterns for analysis.

Architecture:
  - Temporal clustering for pattern detection
  - Semantic similarity for same-type grouping
  - Statistical analysis (average interval, frequency, trend)
  - Representative event selection
        return query.all()

    def delete_recurrence_group(self, db: Session, group_id: str) -> bool:
        """Delete a recurrence group (mark as inactive)."""
        group = db.get(CareEventRecurrenceGroup, group_id)
        if not group:
            return False
        group.is_active = False
        group.status = "inactive"
        db.commit()
        return True
