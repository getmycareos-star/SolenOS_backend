import re

# Read the file
with open(r'C:\solenos\backend\app\services\weekly_care_review.py', 'r') as f:
    content = f.read()

# Define the incomplete function pattern
incomplete_pattern = r'''def _detect_novel_patterns\(self, week_evidence: List\[Evidence\]\) -> List\[Dict\[str, Any\]\]:
        """Detect novel patterns in the week's evidence by analyzing for emerging patterns."""
        patterns = []
        
        if len\(week_evidence\) < 3:
            # Need sufficient evidence to detect patterns
    return patterns'''

# Define the complete function
complete_function = '''    def _detect_novel_patterns(self, week_evidence: List[Evidence]) -> List[Dict[str, Any]]:
        """Detect novel patterns in the week's evidence by analyzing for emerging patterns."""
        patterns = []
        
        if len(week_evidence) < 3:
            # Need sufficient evidence to detect patterns
            return patterns
        
        # Group evidence by type and time to detect temporal patterns
        evidence_by_type = {}
        for evidence in week_evidence:
            evidence_type = evidence.type.value if hasattr(evidence.type, 'value') else str(evidence.type)
            if evidence_type not in evidence_by_type:
                evidence_by_type[evidence_type] = []
            evidence_by_type[evidence_type].append(evidence)
        
        # Detect increasing/decreasing trends in evidence frequency
        for evidence_type, evidences in evidence_by_type.items():
            if len(evidences) >= 3:
                # Sort by time
                sorted_evidences = sorted([e for e in evidences if e.event_time], 
                                        key=lambda x: x.event_time or datetime.min.replace(tzinfo=timezone.utc))
                
                if len(sorted_evidences) >= 3:
                    # Simple trend detection: compare first half vs second half
                    mid_point = len(sorted_evidences) // 2
                    first_half = sorted_evidences[:mid_point]
                    second_half = sorted_evidences[mid_point:]
                    
                    # Check for increasing trend
                    if len(second_half) > len(first_half):
                        patterns.append({
                            "type": "frequency_increase",
                            "evidence_type": evidence_type,
                            "description": f"Increasing frequency of {evidence_type} evidence observed",
                            "confidence": 0.7,
                            "evidence_count": len(evidences),
                            "time_trend": "increasing"
                        })
                    # Check for decreasing trend
                    elif len(first_half) > len(second_half):
                        patterns.append({
                            "type": "frequency_decrease",
                            "evidence_type": evidence_type,
                            "description": f"Decreasing frequency of {evidence_type} evidence observed",
                            "confidence": 0.7,
                            "evidence_count": len(evidences),
                            "time_trend": "decreasing"
                        })
        
        # Detect co-occurrence patterns
        co_occurrence_patterns = self._detect_co_occurrence_patterns(week_evidence)
        patterns.extend(co_occurrence_patterns)
        
        # Detect sequence patterns
        sequence_patterns = self._detect_sequence_patterns(week_evidence)
        patterns.extend(sequence_patterns)
        
        return patterns'''

# Replace the incomplete function with the complete one
content = re.sub(re.escape(incomplete_pattern), complete_function, content, flags=re.DOTALL)

# Write the file back
with open(r'C:\solenos\backend\app\services\weekly_care_review.py', 'w') as f:
    f.write(content)

print("File fixed successfully!")
