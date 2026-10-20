# Read all lines
with open(r'C:\solenos\backend\app\services\weekly_care_review.py', 'r') as f:
    lines = f.readlines()

# Find the incomplete _detect_novel_patterns function
incomplete_start = -1
for i, line in enumerate(lines):
    if line.strip() == 'def _detect_novel_patterns(self, week_evidence: List[Evidence]) -> List[Dict[str, Any]]:':
        incomplete_start = i
        break

if incomplete_start == -1:
    print("Could not find _detect_novel_patterns function")
    exit(1)

# Find the end of the incomplete function (look for a line that starts with 'def ' or is much less indented)
incomplete_end = -1
for i in range(incomplete_start + 1, min(incomplete_start + 50, len(lines))):
    stripped = lines[i].lstrip()
    if stripped.startswith('def ') or (stripped.strip() == '' and i > incomplete_start + 5):
        # Found next function or enough blank lines - assume the function ended before this
        incomplete_end = i - 1
        break
    # Also check if we've reached a line that's not indented as much as the function body
    if not lines[i].startswith('    ') and not lines[i].startswith('\t') and lines[i].strip() != '':
        incomplete_end = i - 1
        break

if incomplete_end == -1:
    # Default to looking a reasonable distance ahead
    incomplete_end = min(incomplete_start + 15, len(lines) - 1)

print(f"Found incomplete function at lines {incomplete_start+1} to {incomplete_end+1}")

# Show what we found
print("Function content:")
for i in range(incomplete_start, incomplete_end + 1):
    print(f"{i+1:3}: {lines[i].rstrip()}")

# Now find the duplicate incomplete pattern later in the file
# We'll look for the same pattern starting after the incomplete_end
duplicate_start = -1
for i in range(incomplete_end + 1, len(lines)):
    if lines[i].strip() == 'def _detect_novel_patterns(self, week_evidence: List[Evidence]) -> List[Dict[str, Any]]:':
        duplicate_start = i
        break

if duplicate_start == -1:
    print("No duplicate _detect_novel_patterns pattern found")
else:
    # Find the end of the duplicate pattern
    duplicate_end = -1
    for i in range(duplicate_start + 1, min(duplicate_start + 50, len(lines))):
        stripped = lines[i].lstrip()
        if stripped.startswith('def ') or (stripped.strip() == '' and i > duplicate_start + 5):
            duplicate_end = i - 1
            break
        # Also check if we've reached a line that's not indented as much as the function body
        if not lines[i].startswith('    ') and not lines[i].startswith('\t') and lines[i].strip() != '':
            duplicate_end = i - 1
            break
    
    if duplicate_end == -1:
        # Default to looking a reasonable distance ahead
        duplicate_end = min(duplicate_start + 15, len(lines) - 1)
    
    print(f"Found duplicate pattern at lines {duplicate_start+1} to {duplicate_end+1}")
    
    # Show what we found
    print("Duplicate content:")
    for i in range(duplicate_start, duplicate_end + 1):
        print(f"{i+1:3}: {lines[i].rstrip()}")

# Define the complete function lines
complete_function_lines = [
    '    def _detect_novel_patterns(self, week_evidence: List[Evidence]) -> List[Dict[str, Any]]:\n',
    '        """Detect novel patterns in the week\'s evidence by analyzing for emerging patterns."""\n',
    '        patterns = []\n',
    '        \n',
    '        if len(week_evidence) < 3:\n',
    '            # Need sufficient evidence to detect patterns\n',
    '            return patterns\n',
    '        \n',
    '        # Group evidence by type and time to detect temporal patterns\n',
    '        evidence_by_type = {}\n',
    '        for evidence in week_evidence:\n',
    '            evidence_type = evidence.type.value if hasattr(evidence.type, \'value\') else str(evidence.type)\n',
    '            if evidence_type not in evidence_by_type:\n',
    '                evidence_by_type[evidence_type] = []\n',
    '            evidence_by_type[evidence_type].append(evidence)\n',
    '        \n',
    '        # Detect increasing/decreasing trends in evidence frequency\n',
    '        for evidence_type, evidences in evidence_by_type.items():\n',
    '            if len(evidences) >= 3:\n',
    '                # Sort by time\n',
    '                sorted_evidences = sorted([e for e in evidences if e.event_time], \n',
    '                                        key=lambda x: x.event_time or datetime.min.replace(tzinfo=timezone.utc))\n',
    '                \n',
    '                if len(sorted_evidences) >= 3:\n',
    '                    # Simple trend detection: compare first half vs second half\n',
    '                    mid_point = len(sorted_evidences) // 2\n',
    '                    first_half = sorted_evidences[:mid_point]\n',
    '                    second_half = sorted_evidences[mid_point:]\n',
    '                    \n',
    '                    # Check for increasing trend\n',
    '                    if len(second_half) > len(first_half):\n',
    '                        patterns.append({\n',
    '                            \"type\": \"frequency_increase\",\n',
    '                            \"evidence_type\": evidence_type,\n',
    '                            \"description\": f\"Increasing frequency of {evidence_type} evidence observed\",\n',
    '                            \"confidence\": 0.7,\n',
    '                            \"evidence_count\": len(evidences),\n',
    '                            \"time_trend\": \"increasing\"\n',
    '                        })\n',
    '                    # Check for decreasing trend\n',
    '                    elif len(first_half) > len(second_half):\n',
    '                        patterns.append({\n',
    '                            \"type\": \"frequency_decrease\",\n',
    '                            \"evidence_type\": evidence_type,\n',
    '                            \"description\": f\"Decreasing frequency of {evidence_type} evidence observed\",\n',
    '                            \"confidence\": 0.7,\n',
    '                            \"evidence_count\": len(evidences),\n',
    '                            \"time_trend\": \"decreasing\"\n',
    '                        })\n',
    '        \n',
    '        # Detect co-occurrence patterns\n',
    '        co_occurrence_patterns = self._detect_co_occurrence_patterns(week_evidence)\n',
    '        patterns.extend(co_occurrence_patterns)\n',
    '        \n',
    '        # Detect sequence patterns\n',
    '        sequence_patterns = self._detect_sequence_patterns(week_evidence)\n',
    '        patterns.extend(sequence_patterns)\n',
    '        \n',
    '        return patterns\n'
]

# Build the new lines list
new_lines = []

# Add everything before the incomplete function
new_lines.extend(lines[:incomplete_start])

# Add the complete function
new_lines.extend(complete_function_lines)

# Add everything after the incomplete function BUT skip the duplicate if it exists
if duplicate_start != -1 and duplicate_end != -1:
    # Skip the incomplete function and the duplicate
    new_lines.extend(lines[incomplete_end+1:duplicate_start])
    new_lines.extend(lines[duplicate_end+1:])
else:
    # No duplicate found, just add everything after the incomplete function
    new_lines.extend(lines[incomplete_end+1:])

# Write the file back
with open(r'C:\solenos\backend\app\services\weekly_care_review.py', 'w') as f:
    f.writelines(new_lines)

print("File fixed successfully!")
