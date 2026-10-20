import sys

file_path = 'C:\\solenos\\backend\\app\\api\\v1.py'
with open(file_path, 'r') as f:
    lines = f.readlines()

# 1. Add import for app.core.security if not present
import_line_index = -1
for i, line in enumerate(lines):
    if line.strip() == 'from app.core.database import get_db':
        import_line_index = i
        break

if import_line_index != -1:
    # Check if the import is already present (look for the next line)
    if import_line_index + 1 < len(lines) and 'from app.core.security' not in lines[import_line_index + 1]:
        lines.insert(import_line_index + 1, 'from app.core.security import get_current_principal, require_care_context_access, require_authenticated, AuthenticatedPrincipal\\n')

# 2. Replace the list_evidence function
# We know the decorator line is '@router.get(\"/evidence/{person_id}\", response_model=List[EvidenceSchema])'
# We'll find it and replace the next two lines (the function definition and the return line)
for i, line in enumerate(lines):
    if line.strip() == '@router.get(\"/evidence/{person_id}\", response_model=List[EvidenceSchema])':
        # The next line is the function definition
        # We'll replace lines[i+1] and lines[i+2] (assuming the function is two lines)
        # But to be safe, we'll replace until we see a line that starts with '@router.get' or '@' or 'def' (for another function) or the end of the file.
        # However, we know the original function is two lines.
        # We'll replace lines[i+1] and lines[i+2] with the new function lines.
        new_func_lines = [
            'def list_evidence(person_id: str, principal: AuthenticatedPrincipal = Depends(get_current_principal), db: Session = Depends(get_db)):\\n',
            '    require_authenticated(principal)\\n',
            '    # Authorization check: ensure the authenticated user can access the person whose evidence is being requested\\n',
            '    require_care_context_access(principal, person_id, \"evidence access\")\\n',
            '    return db.query(EvidenceModel).filter(EvidenceModel.person_id == person_id).all()\\n'
        ]
        # Replace the two lines after the decorator
        lines[i+1:i+3] = new_func_lines
        break

with open(file_path, 'w') as f:
    f.writelines(lines)
