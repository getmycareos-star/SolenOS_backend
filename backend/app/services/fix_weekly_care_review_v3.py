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

# Find the end of the incomplete function (look for the return patterns line that's not indented properly)
incomplete_end = -1
for i in range(incomplete_start, min(incomplete_start + 20, len(lines))):
    if lines[i].rstrip() == 'return patterns':
        incomplete_end = i
        break

if incomplete_end == -1:
    print("Could not find end of _detect_novel_patterns function")
    exit(1)

print(f"Found incomplete function at lines {incomplete_start+1} to {incomplete_end+1}")

# Now find the duplicate incomplete pattern later in the file
# We'll look for the same pattern starting after the incomplete_end
duplicate_start = -1
for i in range(incomplete_end + 1, len(lines)):
    if lines[i].strip() == 'def _detect_novel_patterns(self, week_evidence: List[Evidence]) -> List[Dict[str, Any]]:':
        duplicate_start = i
        break

if duplicate_start == -1:
    print("Could not find duplicate _detect_novel_patterns pattern")
    # This might be OK - maybe there's no duplicate
else:
    # Find the end of the duplicate pattern
    duplicate_end = -1
    for i in range(duplicate_start, min(duplicate_start + 20, len(lines))):
        if lines[i].rstrip() == 'return patterns':
            duplicate_end = i
            break
    
    if duplicate_end == -1:
        print("Could not find end of duplicate _detect_novel_patterns pattern")
    else:
        print(f"Found duplicate pattern at lines {duplicate_start+1} to {duplicate_end+1}")

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
