import sys

file_path = 'C:\\solenos\\backend\\app\\api\\v1.py'
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# We know the decorator is at line 236 (0-indexed) and the function starts at line 237.
# We'll replace lines 237 and 238 (the function definition and the return line) with the new function.
# But note: the new function has more lines.
# We'll replace from line 237 to line 238 (inclusive) with the new function lines.

# However, to be safe, we'll look for the decorator and then replace the next two lines.
# But we already know the line numbers from the previous search.

# Let's use the line numbers we found: decorator at 236, function at 237, and the return line at 238.
# We'll replace lines 237 and 238.

new_func_lines = [
    'def list_evidence(person_id: str, principal: AuthenticatedPrincipal = Depends(get_current_principal), db: Session = Depends(get_db)):\\n',
    '    require_authenticated(principal)\\n',
    '    # Authorization check: ensure the authenticated user can access the person whose evidence is being requested\\n',
    '    require_care_context_access(principal, person_id, \"evidence access\")\\n',
    '    return db.query(EvidenceModel).filter(EvidenceModel.person_id == person_id).all()\\n'
]

print('Number of lines before:', len(lines))
print('Line 236:', repr(lines[236]))
print('Line 237:', repr(lines[237]))
print('Line 238:', repr(lines[238]))

# Replace lines 237 and 238 (index 237 and 238) with the new function lines.
lines[237:239] = new_func_lines

print('Number of lines after:', len(lines))
print('Line 236:', repr(lines[236]))
print('Line 237:', repr(lines[237]))
print('Line 238:', repr(lines[238]))
print('Line 239:', repr(lines[239]))
print('Line 240:', repr(lines[240]))
print('Line 241:', repr(lines[241]))

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)
