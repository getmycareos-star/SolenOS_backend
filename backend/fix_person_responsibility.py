
import re

# Read the file
with open('c:/solenos/backend/app/api/person_responsibility.py', 'r') as f:
    content = f.read()

# Add notification imports if not already present
if 'from app.services.notification import create_notification_service' not in content:
    # Insert after the existing imports (after the last app.schemas.* import)
    # Find a good place to insert - after the last app.schemas.person_responsibility import
    content = content.replace(
        'from app.schemas.person_responsibility import (',
        'from app.schemas.person_responsibility import (\\nfrom app.services.notification import create_notification_service\\nfrom app.schemas.notification import NotificationCreate'
    )

# Add notification in create_person_responsibility endpoint after self.db.add(responsibility) and self.db.flush() and before return responsibility
# Find: self.db.flush() followed by return responsibility
pattern = r'(\\s+self\\.db\\.flush\\(\\)\\s*n\\s+return responsibility)'
# We'll be more specific by looking for the create_person_responsibility context
# For now, let's do a targeted replacement and see if it works
replacement = '''\\n        # Send notification for new responsibilities
        notification_service = create_notification_service(db)
        notification_data = NotificationCreate(
            recipient_person_id=responsibility.person_id,
            type='responsibility_created',
            title='New Responsibility Assigned',
            message=f'You have been assigned responsibility for: {responsibility.title}',
            related_entity_type='person_responsibility',
            related_entity_id=responsibility.id,
            priority='high'
        )
        notification_service.create_notification(notification_data)
        return responsibility'''
content = re.sub(pattern, replacement, content)

# Add notification in assign_person_responsibility endpoint after the assignment logic and before return
# Find: the return statement at the end of the function
# Let's look for the pattern that returns the responsibility
pattern = r'(\\s+return responsibility)'
replacement = '''\\n        # Send notification for responsibility assignment
        notification_service = create_notification_service(db)
        notification_data = NotificationCreate(
            recipient_person_id=responsibility.person_id,
            type='responsibility_assigned',
            title='Responsibility Assignment Updated',
            message=f'Ownership of responsibility changed: {responsibility.title}',
            related_entity_type='person_responsibility',
            related_entity_id=responsibility.id,
            priority='high'
        )
        notification_service.create_notification(notification_data)
        
        # Also notify the new owner if different from the person
        if responsibility.owner_person_id and responsibility.owner_person_id != responsibility.person_id:
            # Notify the caregiver who now owns the responsibility
            notification_data_owner = NotificationCreate(
                recipient_person_id=responsibility.owner_person_id,
                type='responsibility_assigned_to_you',
                title='New Responsibility Assigned',
                message=f'You have been assigned responsibility for: {responsibility.title}',
                related_entity_type='person_responsibility',
                related_entity_id=responsibility.id,
                priority='high'
            )
            notification_service.create_notification(notification_data_owner)
        return responsibility'''
content = re.sub(pattern, replacement, content)

# Write back the file
with open('c:/solenos/backend/app/api/person_responsibility.py', 'w') as f:
    f.write(content)

print('Person responsibility API modified successfully')

