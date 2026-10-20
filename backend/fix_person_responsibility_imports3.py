
import re

# Read the file
with open('c:/solenos/backend/app/api/person_responsibility.py', 'r') as f:
    content = f.read()

# Fix the broken import by replacing the corrupted section with correct syntax
# Find the corrupted line and replace it
content = content.replace(
    'from app.schemas.person_responsibility = (\\nfrom app.services.notification = create_notification_service\\nfrom app.schemas.notification = NotificationCreate',
    'from app.schemas.person_responsibility import ('
)

# Now find where the person_responsibility import block ends and add our imports after
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
        # Add our notification imports after the closing parenthesis - using actual newlines
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

print('Person responsibility API imports properly fixed with actual newlines')

