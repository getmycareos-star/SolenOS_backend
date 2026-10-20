import sys

file_path = 'C:\\solenos\\backend\\app\\api\\v1.py'
with open(file_path, 'r') as f:
    lines = f.readlines()

# Find the line index of the list_evidence function
# We look for the line that starts with '@router.get(\"/evidence/{person_id}\")'
for i, line in enumerate(lines):
    if line.strip() == '@router.get(\"/evidence/{person_id}\")':
        # The next line is the function definition
        # We will replace the function definition and the following lines until we see the next function or decorator.
        # We know the original function is 2 lines (the def line and the return line).
        # But to be safe, we'll replace until we see a line that starts with '@router.get' or '@' or 'def' (for another function) or the end of the file.
        start = i + 1
        end = start
        while end < len(lines) and not (lines[end].strip().startswith('@router.get') or lines[end].strip().startswith('@') or lines[end].strip().startswith('def ')):
            end += 1
        # Now we have the function lines from start to end-1.
        # We will replace them with our new function.
        new_func_lines = [
            'def list_evidence(person_id: str, principal: AuthenticatedPrincipal = Depends(get_current_principal), db: Session = Depends(get_db)):\\n',
            '    require_authenticated(principal)\\n',
            '    # Authorization check: ensure the authenticated user can access the person whose evidence is being requested\\n',
            '    require_care_context_access(principal, person_id, \"evidence access\")\\n',
            '    return db.query(EvidenceModel).filter(EvidenceModel.person_id == person_id).all()\\n'
        ]
        # Replace the lines
        lines[start:end] = new_func_lines
        break

with open(file_path, 'w') as f:
    f.writelines(lines)
