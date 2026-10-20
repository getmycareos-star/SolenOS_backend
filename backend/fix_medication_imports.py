
import re

# Read the file
with open('c:/solenos/backend/app/services/medication.py', 'r') as f:
    content = f.read()

# Fix the imports - remove the incorrect insertion inside the MedicationImport parentheses
content = content.replace(
    'from app.schemas.medication = (\\nfrom app.services.notification import create_notification_service\\nfrom app.schemas.notification import NotificationCreate',
    'from app.schemas.medication import ('
)

# Add notification imports after the MedicationImport block, before the next import or class/function
# Find the end of the MedicationImport block
importEndPattern = r'(from app\\.schemas\\.medication import \\[.*?\\])'
# Actually, let's do this more simply - find the line after the MedicationImport block ends
lines = content.split('\\n')
newLines = []
medicationImportEnded = False
medicationImportFound = False

for line in lines:
    newLines.append(line)
    if 'from app.schemas.medication import (' in line:
        medicationImportFound = True
    elif medicationImportFound and line.strip() == ')':
        # End of the medication import block
        medicationImportEnded = True
        # Add our notification imports after the closing parenthesis
        newLines.append('from app.services.notification import create_notification_service')
        newLines.append('from app.schemas.notification import NotificationCreate')
    elif medicationImportEnded and line.strip() and not line.startswith('from ') and not line.startswith('import ') and not line.startswith('#'):
        # We've passed the import block and hit actual code - reset flags
        medicationImportFound = False
        medicationImportEnded = False

content = '\\n'.join(newLines)

# Add notification in create_medication method after self.db.flush() and before return medication
# Find: self.db.flush() followed by return medication
# We need to be more specific to avoid replacing all instances
# Let's look for the pattern in the create_medication method context
# For simplicity, let's target the specific location after flush and before return in create_medication
# We'll do this by finding the method and then the specific lines

# Actually, let's just do a targeted replacement for the create_medication method
# Find the create_medication method and add notification before the return
createMedPattern = r'(def create_medication\\(self, medication_data: MedicationCreate\\) -> Medication:.*?self\\.db\\.flush\\(\\)\\s*n\\s+return medication)'
# This is getting complex. Let's do a simpler approach - just add after flush in the method
# and we'll accept that it might add to multiple places for now, as long as it doesn't break anything

# For now, let's just verify the file structure is okay and then we can refine if needed
# Let's revert to a simpler approach for now

# Actually, let me just check what we have and fix it manually if needed
with open('c:/solenos/backend/app/services/medication.py', 'w') as f:
    f.write(content)

print('Basic medication service modification done')

