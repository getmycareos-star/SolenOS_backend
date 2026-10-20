import sys
import re

file_path = 'C:\\solenos\\backend\\app\\api\\caregiver_intelligence.py'
with open(file_path, 'r') as f:
    lines = f.readlines()

# Find the line index of 'from app.core.database = get_db'
import_line_index = -1
for i, line in enumerate(lines):
    if line.strip() == 'from app.core.database import get_db':
        import_line_index = i
        break

if import_line_index != -1:
    # Insert the security imports after this line
    lines.insert(import_line_index + 1, 'from app.core.security import get_current_principal, require_care_context_access, require_authenticated, AuthenticatedPrincipal\\n')

# Now find the function get_caregiver_by_email
# We'll look for the line that starts with '@router.get(\"/caregivers/by-email\")'
for i, line in enumerate(lines):
    if line.strip() == '@router.get(\"/caregivers/by-email\")':
        # The next line is the function definition
        # We will replace the function definition and the following lines until we see the next decorator or end of function.
        # We know the original function is 7 lines (including the def line and the return line).
        # But to be safe, we'll replace until we see a line that starts with '@router.get' or '@' or the end of the file.
        # However, we want to keep the decorator line, so we start replacing from the line after the decorator.
        start = i + 1
        # Find the end of the function: look for a line that starts with '@router.get' or '@' or is empty? Actually, we'll look for a line that starts with '@' (for decorator) or the end of the file.
        end = start
        while end < len(lines) and not (lines[end].strip().startswith('@router.get') or lines[end].strip().startswith('@')):
            end += 1
        # Now we have the function lines from start to end-1.
        # We will replace them with our new function.
        new_func_lines = [
            'def get_caregiver_by_email(email: str, principal: AuthenticatedPrincipal = Depends(get_current_principal), db: Session = Depends(get_db)):\\n',
            '    require_authenticated(principal)\\n',
            '    service = CaregiverIntelligenceService(db)\\n',
            '    profile = service.get_caregiver_by_email(email)\\n',
            '    if not profile:\\n',
            '        raise HTTPException(status_code=404, detail=\"Caregiver not found\")\\n',
            '    # Authorization check: ensure the authenticated user can access the person associated with this caregiver\\n',
            '    require_care_context_access(principal, profile.person_id, \"caregiver lookup by email\")\\n',
            '    return {\"id\": profile.id, \"name\": profile.name, \"person_id\": profile.person_id}\\n'
        ]
        # Replace the lines
        lines[start:end] = new_func_lines
        break

with open(file_path, 'w') as f:
    f.writelines(lines)
