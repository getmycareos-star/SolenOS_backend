import sys
import re

file_path = 'C:\\solenos\\backend\\app\\api\\v1.py'
with open(file_path, 'r') as f:
    content = f.read()

# 1. Add imports if not present
if 'from app.core.security' not in content:
    # Insert after the line that contains 'from app.core.database = get_db'
    lines = content.splitlines()
    new_lines = []
    for line in lines:
        new_lines.append(line)
        if line.strip() == 'from app.core.database import get_db':
            new_lines.append('from app.core.security import get_current_principal, require_care_context_access, require_authenticated, AuthenticatedPrincipal')
    content = '\\n'.join(new_lines)

# 2. Replace the list_evidence function
# We'll look for the decorator and the next two lines (the function definition and the return line)
lines = content.splitlines()
new_lines = []
i = 0
while i < len(lines):
    if lines[i] == '@router.get(\"/evidence/{person_id}\", response_model=List[EvidenceSchema])':
        new_lines.append(lines[i])
        i += 1
        # Skip the next two lines (the old function)
        # But we need to make sure we are skipping the correct lines.
        # We'll skip exactly two lines.
        for _ in range(2):
            if i < len(lines):
                i += 1
        # Add the new function lines
        new_lines.extend('''@router.get(\"/evidence/{person_id}\", response_model=List[EvidenceSchema])
def list_evidence(person_id: str, principal: AuthenticatedPrincipal = Depends(get_current_principal), db: Session = Depends(get_db)):
    require_authenticated(principal)
    # Authorization check: ensure the authenticated user can access the person whose evidence is being requested
    require_care_context_access(principal, person_id, \"evidence access\")
    return db.query(EvidenceModel).filter(EvidenceModel.person_id == person_id).all()
'''.splitlines())
    else:
        new_lines.append(lines[i])
        i += 1

content = '\\n'.join(new_lines)

with open(file_path, 'w') as f:
    f.write(content)
