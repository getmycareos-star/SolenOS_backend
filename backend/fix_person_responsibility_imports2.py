
import re

# Read the file
with open('c:/solenos/backend/app/api/person_responsibility.py', 'r') as f:
    content = f.read()

# Find and fix the broken import section
# Look for the pattern that shows the corruption
if 'from app.schemas.person_responsibility = (' in content:
    # Replace the corrupted line with the correct import structure
    content = content.replace(
        'from app.schemas.person_responsibility = (\\nfrom app.services.notification = create_notification_service\\nfrom app.schemas.notification = NotificationCreate',
        'from app.schemas.person_responsibility import ('
    )

# Now, we need to find where the person_responsibility import block ends
# and add our notification imports after the closing parenthesis
lines = content.split('\\n')
newLines = []
personRespImportEnded = False
personRespImportFound = False

for line in lines:
    newLines.append(line)
    if 'from app.schemas.person_responsibility import (' in line:
        personRespImportFound = True
    elif personRespImportFound and line.strip() == ')':
        # End of the person responsibility import block
        personRespImportEnded = True
        # Add our notification imports after the closing parenthesis
        newLines.append('from app.services.notification import create_notification_service')
        newLines.append('from app.schemas.notification import NotificationCreate')
    elif personRespImportEnded and line.strip() and not line.startswith('from ') and not line.startswith('import ') and not line.startswith('#'):
        # We've passed the import block and hit actual code - reset flags
        personRespImportFound = False
        personRespImportEnded = False

content = '\\n'.join(newLines)

# Write back the file
with open('c:/solenos/backend/app/api/person_responsibility.py', 'w') as f:
    f.write(content)

print('Person responsibility API imports properly fixed')

