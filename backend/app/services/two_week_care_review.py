"""Two-Week Care Review Service for SolenOS.

Implements the longitudinal reasoning workflow to help caregivers understand
care trajectory over a two-week period by comparing Day 1 state, current state,
and baseline state.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.services.care_graph_service import CareGraphService
from app.models.care_graph import CareGraphNode, CareGraphEdge, CareGraphNodeVersion, CareGraphEdgeVersion
from app.core.graph_enums import (
    CareGraphNodeType,
    CareGraphRelationshipType,
    CareGraphTemporalStatus,
    CareGraphChangeType,
)
from app.models.identity import Person
from app.models.uncertainty import (
    InformationGap as InformationGapModel,
    OpenQuestion as OpenQuestionModel,
    Contradiction as ContradictionModel,
)
from app.models.caregiver_intelligence import CaregiverResponsibility
from app.core.caregiver_enums import ResponsibilityStatus, AcceptanceStatus


class TwoWeekCareReviewService:
    """Service for generating two-week care reviews."""

    def __init__(self, db: Session):
        self.db = db
        self.care_graph_service = CareGraphService(db)

    def generate_review(
        self,
        person_id: str,
        end_date: Optional[datetime] = None,
        baseline_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Generate a two-week care review for a person.

        Args:
            person_id: The person's ID
            end_date: End of review period (defaults to now)
            baseline_date: Date for baseline comparison (optional)

        Returns:
            Dictionary containing the review results
        """
        if end_date is None:
            end_date = datetime.now(timezone.utc)
        
        start_date = end_date - timedelta(days=14)
        
        # Get states
        day1_state = self._get_state_at_time(person_id, start_date)
        current_state = self._get_state_at_time(person_id, end_date)
        
        # Get baseline state if provided
        baseline_state = None
        if baseline_date:
            baseline_state = self._get_state_at_time(person_id, baseline_date)
        
        # Compare states
        comparison = self._compare_states(day1_state, current_state)
        
        # Detect trajectory patterns
        trajectory = self._detect_trajectory(
            person_id, start_date, end_date, day1_state, current_state
        )
        
        # Identify unresolved issues (open loops)
        open_loops = self._get_open_loops(person_id, start_date, end_date)
        
        # Extract decisions and outcomes
        decisions_outcomes = self._extract_decisions_and_outcomes(person_id, start_date, end_date)
        
        # Extract caregiver workload changes
        workload_changes = self._extract_workload_changes(person_id, start_date, end_date)
        
        # Generate caregiver-facing insights
        insights = self._generate_insights(
            comparison, trajectory, open_loops, day1_state, current_state,
            decisions_outcomes, workload_changes
        )
        
        return {
            "person_id": person_id,
            "review_period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "baseline_date": baseline_date.isoformat() if baseline_date else None,
            },
            "states": {
                "day1": day1_state,
                "current": current_state,
                "baseline": baseline_state,
            },
            "comparison": comparison,
            "trajectory": trajectory,
            "open_loops": open_loops,
            "decisions_outcomes": decisions_outcomes,
            "workload_changes": workload_changes,
            "insights": insights,
        }

    def _get_state_at_time(
        self, person_id: str, target_time: datetime
    ) -> Dict[str, Any]:
        """Reconstruct the care state at a specific point in time.
        
        Returns nodes and edges valid at target_time.
        """
        # Query nodes valid at target_time
        nodes = self.db.query(CareGraphNode).filter(
            CareGraphNode.person_id == person_id,
            CareGraphNode.valid_from <= target_time,
            or_(
                CareGraphNode.valid_to.is_(None),
                CareGraphNode.valid_to >= target_time
            ),
            CareGraphNode.temporal_status == CareGraphTemporalStatus.CURRENT
        ).all()
        
        # Query edges valid at target_time
        edges = self.db.query(CareGraphEdge).filter(
            CareGraphEdge.person_id == person_id,
            CareGraphEdge.valid_from <= target_time,
            or_(
                CareGraphEdge.valid_to.is_(None),
                CareGraphEdge.valid_to >= target_time
            ),
            CareGraphEdge.temporal_status == CareGraphTemporalStatus.CURRENT
        ).all()
        
        return {
            "nodes": [self._node_to_dict(node) for node in nodes],
            "edges": [self._edge_to_dict(edge) for edge in edges],
            "timestamp": target_time.isoformat(),
        }

    def _node_to_dict(self, node: CareGraphNode) -> Dict[str, Any]:
        """Convert a node to a dictionary representation."""
        return {
            "id": str(node.id),
            "type": node.node_type.value if hasattr(node.node_type, 'value') else str(node.node_type),
            "label": node.label,
            "canonical_entity_id": node.canonical_entity_id,
            "canonical_entity_type": node.canonical_entity_type,
            "properties": node.properties or {},
            "provenance": {
                "source": node.provenance_source.value if hasattr(node.provenance_source, 'value') else str(node.provenance_source),
                "details": node.provenance_details or {},
            },
            "valid_from": node.valid_from.isoformat() if node.valid_from else None,
            "valid_to": node.valid_to.isoformat() if node.valid_to else None,
        }

    def _edge_to_dict(self, edge: CareGraphEdge) -> Dict[str, Any]:
        """Convert an edge to a dictionary representation."""
        return {
            "id": str(edge.id),
            "source_node_id": str(edge.source_node_id),
            "target_node_id": str(edge.target_node_id),
            "type": edge.relationship_type.value if hasattr(edge.relationship_type, 'value') else str(edge.relationship_type),
            "subtype": edge.relationship_subtype.value if hasattr(edge.relationship_subtype, 'value') else str(edge.relationship_subtype),
            "status": edge.status.value if hasattr(edge.status, 'value') else str(edge.status),
            "properties": edge.properties or {},
            "provenance": {
                "source": edge.provenance_source.value if hasattr(edge.provenance_source, 'value') else str(edge.provenance_source),
                "details": edge.provenance_details or {},
            },
            "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
            "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        }

    def _compare_states(
        self, state1: Dict[str, Any], state2: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Compare two care states and identify changes.
        
        Returns:
            Dictionary with added, removed, changed items and unchanged count.
        """
        # Create lookup dictionaries for efficient comparison
        nodes1 = {n["id"]: n for n in state1["nodes"]}
        nodes2 = {n["id"]: n for n in state2["nodes"]}
        edges1 = {e["id"]: e for e in state1["edges"]}
        edges2 = {e["id"]: e for e in state2["edges"]}
        
        # Find added and removed nodes
        added_nodes = [n2 for nid, n2 in nodes2.items() if nid not in nodes1]
        removed_nodes = [n1 for nid, n1 in nodes1.items() if nid not in nodes2]
        
        # Find changed nodes (same ID but different properties)
        changed_nodes = []
        for nid in set(nodes1.keys()) & set(nodes2.keys()):
            n1, n2 = nodes1[nid], nodes2[nid]
            if self._nodes_differ(n1, n2):
                changed_nodes.append({"before": n1, "after": n2})
        
        # Same for edges
        added_edges = [e2 for eid, e2 in edges2.items() if eid not in edges1]
        removed_edges = [e1 for eid, e1 in edges1.items() if eid not in edges2]
        changed_edges = []
        for eid in set(edges1.keys()) & set(edges2.keys()):
            e1, e2 = edges1[eid], edges2[eid]
            if self._edges_differ(e1, e2):
                changed_edges.append({"before": e1, "after": e2})
        
        return {
            "nodes": {
                "added": added_nodes,
                "removed": removed_nodes,
                "changed": changed_nodes,
                "unchanged_count": len(set(nodes1.keys()) & set(nodes2.keys())) - len(changed_nodes),
            },
            "edges": {
                "added": added_edges,
                "removed": removed_edges,
                "changed": changed_edges,
                "unchanged_count": len(set(edges1.keys()) & set(edges2.keys())) - len(changed_edges),
            },
        }

    def _nodes_differ(self, node1: Dict[str, Any], node2: Dict[str, Any]) -> bool:
        """Check if two nodes have meaningful differences."""
        # Compare labels, canonical IDs, and properties
        if node1.get("label") != node2.get("label"):
            return True
        if node1.get("canonical_entity_id") != node2.get("canonical_entity_id"):
            return True
        if node1.get("canonical_entity_type") != node2.get("canonical_entity_type"):
            return True
        # Compare properties (simplified)
        props1 = node1.get("properties", {})
        props2 = node2.get("properties", {})
        if props1 != props2:
            return True
        return False

    def _edges_differ(self, edge1: Dict[str, Any], edge2: Dict[str, Any]) -> bool:
        """Check if two edges have meaningful differences."""
        # Compare status, subtype, and properties
        if edge1.get("status") != edge2.get("status"):
            return True
        if edge1.get("subtype") != edge2.get("subtype"):
            return True
        # Compare properties
        props1 = edge1.get("properties", {})
        props2 = edge2.get("properties", {})
        if props1 != props2:
            return True
        return False

    def _detect_trajectory(
        self,
        person_id: str,
        start_date: datetime,
        end_date: datetime,
        day1_state: Dict[str, Any],
        current_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Detect trajectory patterns over the review period using version history."""
        
        # Get node versions that changed during the period
        node_versions = self.db.query(CareGraphNodeVersion).join(
            CareGraphNode, CareGraphNodeVersion.node_id == CareGraphNode.id
        ).filter(
            CareGraphNode.person_id == person_id,
            CareGraphNodeVersion.created_at >= start_date,
            CareGraphNodeVersion.created_at <= end_date
        ).order_by(CareGraphNodeVersion.created_at).all()
        
        # Get edge versions that changed during the period
        edge_versions = self.db.query(CareGraphEdgeVersion).join(
            CareGraphEdge, CareGraphEdgeVersion.edge_id == CareGraphEdge.id
        ).filter(
            CareGraphEdge.person_id == person_id,
            CareGraphEdgeVersion.created_at >= start_date,
            CareGraphEdgeVersion.created_at <= end_date
        ).order_by(CareGraphEdgeVersion.created_at).all()
        
        # Analyze changes to identify patterns
        persistent_changes = []
        temporary_changes = []
        recurring_patterns = []
        improvements = []
        worsening = []
        stable = []
        
        # Analyze node version sequences for patterns
        if node_versions:
            node_patterns = self._analyze_version_sequences(node_versions, "node")
            persistent_changes.extend(node_patterns.get("persistent", []))
            temporary_changes.extend(node_patterns.get("temporary", []))
            recurring_patterns.extend(node_patterns.get("recurring", []))
            improvements.extend(node_patterns.get("improvements", []))
            worsening.extend(node_patterns.get("worsening", []))
            stable.extend(node_patterns.get("stable", []))
        
        # Analyze edge version sequences for patterns
        if edge_versions:
            edge_patterns = self._analyze_version_sequences(edge_versions, "edge")
            persistent_changes.extend(edge_patterns.get("persistent", []))
            temporary_changes.extend(edge_patterns.get("temporary", []))
            recurring_patterns.extend(edge_patterns.get("recurring", []))
            improvements.extend(edge_patterns.get("improvements", []))
            worsening.extend(edge_patterns.get("worsening", []))
            stable.extend(edge_patterns.get("stable", []))
        
        # Also include overall stability assessment
        stability_score = self._calculate_stability_score(day1_state, current_state)
        if stability_score > 0.8:
            stable.append({
                "type": "overall_stability",
                "description": "Overall care state remained stable over the two-week period",
                "stability_score": stability_score
            })
        
        return {
            "persistent_changes": persistent_changes,
            "temporary_changes": temporary_changes,
            "recurring_patterns": recurring_patterns,
            "improvements": improvements,
            "worsening": worsening,
            "stable": stable,
            "node_changes_count": len(node_versions),
            "edge_changes_count": len(edge_versions),
            "trajectory_analysis": {
                "stability_score": stability_score,
                "total_version_changes": len(node_versions) + len(edge_versions),
                "change_distribution": {
                    "persistent": len(persistent_changes),
                    "temporary": len(temporary_changes),
                    "recurring": len(recurring_patterns),
                    "improvement": len(improvements),
                    "worsening": len(worsening),
                    "stable": len(stable)
                }
            }
        }

    def _get_open_loops(
        self, person_id: str, start_date: datetime, end_date: datetime
    ) -> List[Dict[str, Any]]:
        """Get unresolved open loops during the review period.
        
        Integrates with the canonical Open Loop/uncertainty system.
        """
        open_loops = []
        
        # Get open information gaps
        info_gaps = self.db.query(InformationGapModel).filter(
            InformationGapModel.person_id == person_id,
            InformationGapModel.lifecycle_status == "open",
            InformationGapModel.created_at >= start_date,
            InformationGapModel.created_at <= end_date
        ).all()
        
        for gap in info_gaps:
            open_loops.append({
                "type": "information_gap",
                "id": str(gap.id),
                "field": gap.field,
                "subject_type": gap.subject_type,
                "subject_id": gap.subject_id,
                "description": gap.description,
                "gap_reason": gap.gap_reason.value if hasattr(gap.gap_reason, 'value') else str(gap.gap_reason),
                "priority": gap.priority.value if hasattr(gap.priority, 'value') else str(gap.priority),
                "created_at": gap.created_at.isoformat() if gap.created_at else None,
                "expected_by": gap.expected_by.isoformat() if gap.expected_by else None,
            })
        
        # Get open questions
        open_questions = self.db.query(OpenQuestionModel).filter(
            OpenQuestionModel.person_id == person_id,
            OpenQuestionModel.status == "open",
            OpenQuestionModel.asked_at >= start_date,
            OpenQuestionModel.asked_at <= end_date
        ).all()
        
        for question in open_questions:
            open_loops.append({
                "type": "open_question",
                "id": str(question.id),
                "question": question.question,
                "subject_type": question.subject_type,
                "subject_id": question.subject_id,
                "context": question.context or {},
                "priority": question.priority.value if hasattr(question.priority, 'value') else str(question.priority),
                "asked_at": question.asked_at.isoformat() if question.asked_at else None,
                "evidence_ids": question.evidence_ids or [],
            })
        
        # Get open contradictions
        open_contradictions = self.db.query(ContradictionModel).filter(
            ContradictionModel.person_id == person_id,
            ContradictionModel.resolution_status == "unresolved",
            ContradictionModel.created_at >= start_date,
            ContradictionModel.created_at <= end_date
        ).all()
        
        for contradiction in open_contradictions:
            open_loops.append({
                "type": "contradiction",
                "id": str(contradiction.id),
                "field": contradiction.field,
                "subject_type": contradiction.subject_type,
                "subject_id": contradiction.subject_id,
                "description": contradiction.description,
                "resolution_status": contradiction.resolution_status,
                "created_at": contradiction.created_at.isoformat() if contradiction.created_at else None,
                "evidence_ids": contradiction.evidence_ids or [],
            })
        
        return open_loops

    def _extract_decisions_and_outcomes(
        self, person_id: str, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """Extract decisions and outcomes made during the review period."""
        
        # Find CARE_GOAL and OUTCOME nodes created/updated during the period
        goal_nodes = self.db.query(CareGraphNode).filter(
            CareGraphNode.person_id == person_id,
            CareGraphNode.node_type == CareGraphNodeType.CARE_GOAL,
            or_(
                and_(
                    CareGraphNode.valid_from >= start_date,
                    CareGraphNode.valid_from <= end_date
                ),
                and_(
                    CareGraphNode.updated_at >= start_date,
                    CareGraphNode.updated_at <= end_date
                )
            )
        ).all()
        
        outcome_nodes = self.db.query(CareGraphNode).filter(
            CareGraphNode.person_id == person_id,
            CareGraphNode.node_type == CareGraphNodeType.OUTCOME,
            or_(
                and_(
                    CareGraphNode.valid_from >= start_date,
                    CareGraphNode.valid_from <= end_date
                ),
                and_(
                    CareGraphNode.updated_at >= start_date,
                    CareGraphNode.updated_at <= end_date
                )
            )
        ).all()
        
        # Find outcome-related edges
        outcome_edges = self.db.query(CareGraphEdge).filter(
            CareGraphEdge.person_id == person_id,
            or_(
                CareGraphEdge.relationship_type == CareGraphRelationshipType.EVENT_FOLLOWED_BY_OUTCOME,
                CareGraphEdge.relationship_type == CareGraphRelationshipType.INTERVENTION_ASSOCIATED_WITH_OUTCOME,
                CareGraphEdge.relationship_type == CareGraphRelationshipType.MEDICATION_CHANGE_FOLLOWED_BY_OUTCOME,
                CareGraphEdge.relationship_type == CareGraphRelationshipType.APPOINTMENT_PRODUCED_OUTCOME,
                CareGraphEdge.relationship_type == CareGraphRelationshipType.CARE_ACTION_PRODUCED_OUTCOME,
            ),
            or_(
                and_(
                    CareGraphEdge.valid_from >= start_date,
                    CareGraphEdge.valid_from <= end_date
                ),
                and_(
                    CareGraphEdge.updated_at >= start_date,
                    CareGraphEdge.updated_at <= end_date
                )
            )
        ).all()
        
        return {
            "goals_decisions": [self._node_to_dict(node) for node in goal_nodes],
            "outcomes": [self._node_to_dict(node) for node in outcome_nodes],
            "outcome_relationships": [self._edge_to_dict(edge) for edge in outcome_edges],
            "decisions_count": len(goal_nodes),
            "outcomes_count": len(outcome_nodes),
        }

    def _extract_workload_changes(
        self, person_id: str, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """Extract caregiver workload and responsibility changes."""
        
        # Find responsibility changes during the period
        responsibility_changes = self.db.query(CaregiverResponsibility).filter(
            CaregiverResponsibility.person_id == person_id,
            or_(
                and_(
                    CaregiverResponsibility.effective_start >= start_date,
                    CaregiverResponsibility.effective_start <= end_date
                ),
                and_(
                    CaregiverResponsibility.updated_at >= start_date,
                    CaregiverResponsibility.updated_at <= end_date
                )
            )
        ).all()
        
        # Get current responsibilities as of end_date for comparison
        current_responsibilities = self.db.query(CaregiverResponsibility).filter(
            CaregiverResponsibility.person_id == person_id,
            CaregiverResponsibility.effective_start <= end_date,
            or_(
                CaregiverResponsibility.effective_end.is_(None),
                CaregiverResponsibility.effective_end >= end_date
            ),
            CaregiverResponsibility.status == ResponsibilityStatus.ACTIVE
        ).all()
        
        # Get responsibilities as of start_date for comparison
        start_responsibilities = self.db.query(CaregiverResponsibility).filter(
            CaregiverResponsibility.person_id == person_id,
            CaregiverResponsibility.effective_start <= start_date,
            or_(
                CaregiverResponsibility.effective_end.is_(None),
                CaregiverResponsibility.effective_end >= start_date
            ),
            CaregiverResponsibility.status == ResponsibilityStatus.ACTIVE
        ).all()
        
        return {
            "responsibility_changes_during_period": [
                {
                    "id": str(resp.id),
                    "responsibility_type": resp.responsibility_type,
                    "scope": resp.scope,
                    "effective_start": resp.effective_start.isoformat() if resp.effective_start else None,
                    "effective_end": resp.effective_end.isoformat() if resp.effective_end else None,
                    "status": resp.status.value if hasattr(resp.status, 'value') else str(resp.status),
                    "acceptance_status": resp.acceptance_status.value if hasattr(resp.acceptance_status, 'value') else str(resp.acceptance_status),
                    "is_joint": resp.is_joint,
                    "created_at": resp.created_at.isoformat() if resp.created_at else None,
                    "updated_at": resp.updated_at.isoformat() if resp.updated_at else None,
                }
                for resp in responsibility_changes
            ],
            "current_responsibilities_count": len(current_responsibilities),
            "start_responsibilities_count": len(start_responsibilities),
            "net_change": len(current_responsibilities) - len(start_responsibilities),
            "current_responsibilities": [
                {
                    "id": str(resp.id),
                    "responsibility_type": resp.responsibility_type,
                    "scope": resp.scope,
                    "status": resp.status.value if hasattr(resp.status, 'value') else str(resp.status),
                }
                for resp in current_responsibilities
            ]
        }

    def _generate_insights(
        self,
        comparison: Dict[str, Any],
        trajectory: Dict[str, Any],
        open_loops: List[Dict[str, Any]],
        day1_state: Dict[str, Any],
        current_state: Dict[str, Any],
        decisions_outcomes: Dict[str, Any],
        workload_changes: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate caregiver-facing insights answering the core questions."""
        
        # What changed in two weeks?
        changed_items = []
        
        # Node changes
        changed_items.extend([f"Added: {n['label']}" for n in comparison["nodes"]["added"][:5]])
        changed_items.extend([f"Removed: {n['label']}" for n in comparison["nodes"]["removed"][:5]])
        changed_items.extend([f"Modified: {n['before']['label']}" for n in comparison["nodes"]["changed"][:5]])
        
        # Edge changes
        changed_items.extend([f"Added relationship: {e['type']}" for e in comparison["edges"]["added"][:5]])
        changed_items.extend([f"Removed relationship: {e['type']}" for e in comparison["edges"]["removed"][:5]])
        changed_items.extend([f"Changed relationship: {e['before']['type']}" for e in comparison["edges"]["changed"][:5]])
        
        # Decisions and outcomes
        if decisions_outcomes["goals_decisions"]:
            changed_items.append(f"New decisions/goals set: {len(decisions_outcomes['goals_decisions'])}")
        if decisions_outcomes["outcomes"]:
            changed_items.append(f"New outcomes recorded: {len(decisions_outcomes['outcomes'])}")
        
        # Workload changes
        if workload_changes["net_change"] != 0:
            direction = "increased" if workload_changes["net_change"] > 0 else "decreased"
            changed_items.append(f"Caregiver workload {direction} by {abs(workload_changes['net_change'])} responsibilities")
        
        # What matters now? (based on current state and open loops)
        matters_now = []
        
        # High priority open loops
        high_priority_loops = [loop for loop in open_loops if loop.get("priority") in ["high", "critical"] or 
                              (isinstance(loop.get("priority"), str) and loop["priority"].lower() in ["high", "critical"])]
        if high_priority_loops:
            matters_now.append(f"{len(high_priority_loops)} high-priority unresolved issues needing immediate attention")
        
        # Recent significant changes
        if len(comparison["nodes"]["added"]) > 3 or len(comparison["nodes"]["removed"]) > 3:
            matters_now.append("Significant changes in care entity landscape")
        
        # Open contradictions
        contradiction_loops = [loop for loop in open_loops if loop["type"] == "contradiction"]
        if contradiction_loops:
            matters_now.append(f"{len(contradiction_loops)} unresolved contradictions requiring resolution")
        
        # Pending decisions with expected outcomes
        if decisions_outcomes["goals_decisions"]:
            matters_now.append(f"{len(decisions_outcomes['goals_decisions'])} recent decisions awaiting outcome evaluation")
        
        # Workload alerts
        if workload_changes["net_change"] > 2:
            matters_now.append("Significant increase in caregiver responsibilities may require support evaluation")
        elif workload_changes["net_change"] < -2:
            matters_now.append("Decrease in responsibilities may indicate changing care needs or transitions")
        
        # What are we missing? (evidence gaps)
        missing_evidence = []
        
        # Information gaps without expected resolution dates
        unresolved_gaps = [loop for loop in open_loops if loop["type"] == "information_gap" and not loop.get("expected_by")]
        if unresolved_gaps:
            missing_evidence.append(f"{len(unresolved_gaps)} information gaps without expected resolution timelines")
        
        # Open questions without evidence
        questions_without_evidence = [loop for loop in open_loops if loop["type"] == "open_question" and not loop.get("evidence_ids")]  
        if questions_without_evidence:
            missing_evidence.append(f"{len(questions_without_evidence)} open questions lacking supporting evidence")
        
        # Contradictions without resolution path
        unresolved_contradictions = [loop for loop in open_loops if loop["type"] == "contradiction" and loop["resolution_status"] == "unresolved"]
        if unresolved_contradictions:
            missing_evidence.append(f"{len(unresolved_contradictions)} contradictions without documented resolution approach")
        
        # What should we ask next? (question candidates)
        questions = []
        
        # Generate from open loops
        for gap in unresolved_gaps[:3]:  # Top 3
            questions.append({
                "type": "information_gap",
                "question": f"Can we obtain more information about {gap['field']} to resolve this knowledge gap?",
                "source": f"Information gap identified on {gap['created_at'][:10] if gap['created_at'] else 'unknown date'}",
                "priority": "high" if gap.get("priority") in ["high", "critical"] else "medium"
            })
        
        for question in [loop for loop in open_loops if loop["type"] == "open_question"][:3]:  # Top 3
            questions.append({
                "type": "open_question",
                "question": question["question"],
                "source": f"Open question asked on {question['asked_at'][:10] if question['asked_at'] else 'unknown date'}",
                "priority": question.get("priority", "medium")
            })
        
        # Generate from contradictions
        for contradiction in unresolved_contradictions[:2]:  # Top 2
            questions.append({
                "type": "contradiction_resolution",
                "question": f"How should we resolve the contradiction regarding {contradiction['field']}?",
                "source": f"Contradiction identified on {contradiction['created_at'][:10] if contradiction['created_at'] else 'unknown date'}",
                "priority": "high"
            })
        
        # Generate from responsibility changes
        if workload_changes["net_change"] != 0:
            questions.append({
                "type": "workload_assessment",
                "question": "How have recent responsibility changes affected caregiver well-being and care quality?",
                "source": f"Workload changed by {workload_changes['net_change']} responsibilities during review period",
                "priority": "medium" if abs(workload_changes["net_change"]) > 2 else "low"
            })
        
        # Generate from missing outcome follow-up
        if decisions_outcomes["goals_decisions"] and not decisions_outcomes["outcomes"]:
            questions.append({
                "type": "outcome_evaluation",
                "question": "What are the outcomes of recent decisions or goals set during this period?",
                "source": f"{len(decisions_outcomes['goals_decisions'])} decisions made without recorded outcomes yet",
                "priority": "medium"
            })
        
        return {
            "what_changed": changed_items[:15],  # Limit to top 15
            "what_matters_now": matters_now[:8],  # Limit to top 8
            "what_are_we_missing": missing_evidence[:6],  # Limit to top 6
            "what_should_we_ask_next": questions[:10],  # Limit to top 10
            "summary_statistics": {
                "total_changes": len(comparison["nodes"]["added"]) + len(comparison["nodes"]["removed"]) + len(comparison["nodes"]["changed"]) +
                               len(comparison["edges"]["added"]) + len(comparison["edges"]["removed"]) + len(comparison["edges"]["changed"]),
                "open_issues_count": len(open_loops),
                "decisions_made": len(decisions_outcomes["goals_decisions"]),
                "outcomes_recorded": len(decisions_outcomes["outcomes"]),
                "responsibility_net_change": workload_changes["net_change"],
            }
        }
