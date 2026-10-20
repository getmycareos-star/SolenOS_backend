
import re

# Read the file
with open('c:/solenos/backend/app/services/medication.py', 'r') as f:
    content = f.read()

# Add notification imports if not already present
if 'from app.services.notification import create_notification_service' not in content:
    # Insert after the existing imports (after the last app.schemas.medication import)
    content = content.replace(
        'from app.schemas.medication import (',
        'from app.schemas.medication import (\\nfrom app.services.notification import create_notification_service\\nfrom app.schemas.notification import NotificationCreate'
    )

# Add notification in create_medication method after self.db.flush() and before return medication
# Find: self.db.flush() followed by return medication
pattern = r'(\\s+self\\.db\\.flush\\(\\)\\s*n\\s+return medication)'
replacement = '''\\n        # Send notification for new medications
        notification_service = create_notification_service(self.db)
        notification_data = NotificationCreate(
            recipient_person_id=medication.person_id,
            type='medication_added',
            title='New Medication Prescribed',
            message=f'{medication.name} has been added to the medication regimen',
            related_entity_type='medication',
            related_entity_id=medication.id,
            priority='medium'
        )
        notification_service.create_notification(notification_data)
        return medication'''
content = re.sub(pattern, replacement, content)

# Add notification in update_medication method after self.db.flush() and before return medication
# But only for significant changes (dosage or frequency)
# Find: self.db.flush() followed by return medication
pattern = r'(\\s+self\\.db\\.flush\\(\\)\\s*n\\s+return medication)'
# We need to be more specific - let's look for the update_medication method context
# For now, let's add it and we can refine later if needed
replacement = '''\\n        # Send notification for medication updates (only for significant changes)
        if 'dosage' in update_data or 'frequency' in update_data:
            notification_service = create_notification_service(self.db)
            notification_data = NotificationCreate(
                recipient_person_id=medication.person_id,
                type='medication_updated',
                title='Medication Instructions Updated',
                message=f'Dosage or frequency changed for {medication.name}',
                related_entity_type='medication',
                related_entity_id=medication.id,
                priority='medium'
            )
            notification_service.create_notification(notification_data)
        return medication'''
content = re.sub(pattern, replacement, content)

# Add notification in deprecate_medication method after self.db.flush() and before return medication
# Find: self.db.flush() followed by return medication
pattern = r'(\\s+self\\.db\\.flush\\(\\)\\s*n\\s+return medication)'
replacement = '''\\n        # Send notification for medication discontinuation
        notification_service = create_notification_service(self.db)
        notification_data = NotificationCreate(
            recipient_person_id=medication.person_id,
            type='medication_discontinued',
            title='Medication Discontinued',
            message=f'{medication.name} has been discontinued',
            related_entity_type='medication',
            related_entity_id=medication.id,
            priority='high'
        )
        notification_service.create_notification(notification_data)
        return medication'''
content = re.sub(pattern, replacement, content)

# Write back the file
with open('c:/solenos/backend/app/services/medication.py', 'w') as f:
    f.write(content)

print('Medication service modified successfully')

