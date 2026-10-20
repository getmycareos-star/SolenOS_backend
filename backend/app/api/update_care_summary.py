import sys

file_path = 'C:\\solenos\\backend\\app\\api\\care_summary.py'
with open(file_path, 'r') as f:
    lines = f.readlines()

# Find the decorator for export_json and replace the function
for i, line in enumerate(lines):
    if line.strip() == '@export_router.get(\"{person_id}/json\")':
        # Find the end of the function: look for the next line that starts with '@export_router.get' or '@'
        j = i + 1
        while j < len(lines) and not (lines[j].strip().startswith('@export_router.get') or lines[j].strip().startswith('@')):
            j += 1
        # Replace the function body (from i+1 to j-1) with the new function
        new_json_lines = [
            'def export_json(\\n',
            '    person_id: str,\\n',
            '    window_days: int = 30,\\n',
            '    caregiver_id: Optional[str] = None,\\n',
            '    principal: AuthenticatedPrincipal = Depends(get_current_principal),\\n',
            '    db: Session = Depends(get_db),\\n',
            '):\\n',
            '    \"\"\"The structured record, for another system or a GP portal.\"\"\"\\n',
            '    require_authenticated(principal)\\n',
            '    if caregiver_id is None:\\n',
            '        raise HTTPException(status_code=400, detail=\"caregiver_id is required\")\\n',
            '    # Check that the principal is authorized to act as this caregiver for the person\\n',
            '    caregiver_role = principal.caregiver_role_for(person_id)\\n',
            '    if caregiver_role is None or caregiver_role != caregiver_id:\\n',
            '        raise HTTPException(status_code=403, detail=\"Not authorized to act as this caregiver for the person\")\\n',
            '    return _build_or_refuse(person_id, window_days, caregiver_id, db)\\n'
        ]
        lines[i+1:j] = new_json_lines
        break

# Find the decorator for export_pdf and replace the function
for i, line in enumerate(lines):
    if line.strip() == '@export_router.get(\"{person_id}/pdf\")':
        # Find the end of the function: look for the next line that starts with '@export_router.get' or '@'
        j = i + 1
        while j < len(lines) and not (lines[j].strip().startswith('@export_router.get') or lines[j].strip().startswith('@')):
            j += 1
        # Replace the function body (from i+1 to j-1) with the new function
        new_pdf_lines = [
            'def export_pdf(\\n',
            '    person_id: str,\\n',
            '    window_days: int = 30,\\n',
            '    caregiver_id: Optional[str] = None,\\n',
            '    principal: AuthenticatedPrincipal = Depends(get_current_principal),\\n',
            '    db: Session = Depends(get_db),\\n',
            '):\\n',
            '    \"\"\"One-tap PDF for an appointment.\\n\\n    Served as an attachment rather than rendered in a page, because a clinician\\n    wants a document they can keep, not a screen they have to read on a phone in\\n    a waiting room.\\n    \"\"\"\\n',
            '    require_authenticated(principal)\\n',
            '    if caregiver_id is None:\\n',
            '        raise HTTPException(status_code=400, detail=\"caregiver_id is required\")\\n',
            '    # Check that the principal is authorized to act as this caregiver for the person\\n',
            '    caregiver_role = principal.caregiver_role_for(person_id)\\n',
            '    if caregiver_role is None or caregiver_role != caregiver_id:\\n',
            '        raise HTTPException(status_code=403, detail=\"Not authorized to act as this caregiver for the person\")\\n',
            '    summary = _build_or_refuse(person_id, window_days, caregiver_id, db)\\n',
            '    \\n',
            '    try:\\n',
            '        pdf_bytes = render_summary_pdf(summary)\\n',
            '    except Exception as exc:  # pragma: no cover - reportlab layout failure\\n',
            '        raise HTTPException(status_code=500, detail=f\"could not render the summary: {exc}\")\\n',
            '    \\n',
            '    safe_name = \"\\\".join(\\n',
            '        ch if ch.isalnum() or ch in \"-_\" else \"-\" for ch in (summary[\"person\"][\"name\"] or \"person\"))\\n',
            '    return Response(\\n',
            '        content=pdf_bytes,\\n',
            '        media_type=\"application/pdf\",\\n',
            '        headers={\\n',
            '            \"Content-Disposition\": \\'attachment; filename=\"care-summary-{}.pdf\\\"\\.format(safe_name)\\n',
            '        },\\n',
            '    )\\n'
        ]
        lines[i+1:j] = new_pdf_lines
        break

with open(file_path, 'w') as f:
    f.writelines(lines)
