"""Explainability service for producing provenance and uncertainty traces with actual decision artifact traversal."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.models.evidence import Evidence
from app.models.knowledge_revision import KnowledgeClaim
from app.models.care_graph import CareGraphNode, CareGraphEdge
from app.models.belief_state import BeliefState
from app.models.care_recommendations import CareRecommendation
from app.models.evidence import EvidenceVersion
from app.models.care_graph import CareGraphNodeVersion, CareGraphEdgeVersion


class ExplainabilityService:
    """Service for generating explanations of decisions."""

    def __init__(self, db: Session):
        self.db = db

    def get_provenance_chain(self, decision_id: str) -> Dict[str, Any]:
        """Walk back from a decision to its constituent evidence and claims.
        
        Enhanced version that traces through actual decision artifacts and versioned entities.
        """
        # Try to find the decision as a CareRecommendation first
        decision = None
        decision_type = None
        
        # Look for CareRecommendation
        decision = self.db.query(CareRecommendation).filter(
            CareRecommendation.id == decision_id
        ).first()
        
        if decision:
            decision_type = "CareRecommendation"
        else:
            # If not found as recommendation, we could check other decision types
            # For now, we'll treat it as a generic decision ID and try to find related evidence
            pass
        
        # Build provenance chain
        provenance_evidence = self._get_evidence_provenance(decision_id, decision_type)
        provenance_claims = self._get_claims_provenance(decision_id, decision_type)
        provenance_graph = self._get_graph_provenance(decision_id, decision_type)
        
        # Calculate uncertainty based on evidence quality and recency
        uncertainty = self._calculate_uncertainty(provenance_evidence, provenance_claims)
        
        return {
            "decision_id": decision_id,
            "decision_type": decision_type or "unknown",
            "decision_details": self._get_decision_details(decision) if decision else None,
            "provenance": {
                "evidence": provenance_evidence,
                "claims": provenance_claims,
                "graph_paths": provenance_graph,
            },
            "uncertainty": uncertainty,
            "explanation_generated_at": datetime.now(timezone.utc).isoformat()
        }

    def _get_decision_details(self, decision: Optional[CareRecommendation]) -> Optional[Dict[str, Any]]:
        """Extract details from a decision object."""
        if not decision:
            return None
        return {
            "id": decision.id,
            "text": decision.recommendation_text,
            "type": decision.recommendation_type,
            "created_at": decision.created_at.isoformat() if decision.created_at else None,
            "updated_at": decision.updated_at.isoformat() if decision.updated_at else None,
            "person_id": decision.person_id
        }

    def _get_evidence_provenance(self, decision_id: str, decision_type: Optional[str]) -> List[Dict[str, Any]]:
        """Trace evidence that contributed to a decision."""
        evidence_list = []
        
        if decision_type == "CareRecommendation":
            # Get the recommendation
            recommendation = self.db.query(CareRecommendation).filter(
                CareRecommendation.id == decision_id
            ).first()
            
            if recommendation:
                # In a real system, we would have explicit links from recommendations to evidence
                # For now, we'll find evidence related to the person around the time of the recommendation
                person_id = recommendation.person_id
                rec_time = recommendation.created_at
                
                # Look for evidence from a time window around the recommendation
                time_window_hours = 24  # Look at evidence from the last 24 hours
                start_time = rec_time - datetime.timedelta(hours=time_window_hours) if rec_time else None
                
                evidence_query = self.db.query(Evidence).filter(
                    Evidence.person_id == person_id
                )
                
                if start_time:
                    evidence_query = evidence_query.filter(
                        Evidence.event_time >= start_time
                    )
                
                # Get recent evidence
                recent_evidence = evidence_query.order_by(
                    Evidence.event_time.desc()
                ).limit(10).all()
                
                for ev in recent_evidence:
                    evidence_list.append({
                        "id": ev.id,
                        "type": ev.type,
                        "text": ev.source_text[:200] + "..." if len(ev.source_text or "") > 200 else ev.source_text,
                        "confidence": self._estimate_evidence_confidence(ev),
                        "timestamp": ev.event_time.isoformat() if ev.event_time else None,
                        "source_type": ev.source_type,
                        "uploaded_by": ev.uploaded_by_caregiver_id
                    })
        
        # Also check for evidence that might be linked through knowledge claims or other means
        # This would be expanded in a real implementation with explicit provenance links
        
        return evidence_list

    def _get_claims_provenance(self, decision_id: str, decision_type: Optional[str]) -> List[Dict[str, Any]]:
        """Trace knowledge claims that contributed to a decision."""
        claims_list = []
        
        if decision_type == "CareRecommendation":
            # Get the recommendation
            recommendation = self.db.query(CareRecommendation).filter(
                CareRecommendation.id == decision_id
            ).first()
            
            if recommendation:
                person_id = recommendation.person_id
                # Find knowledge claims related to this person
                claims = self.db.query(KnowledgeClaim).filter(
                    KnowledgeClaim.person_id == person_id
                ).order_by(
                    KnowledgeClaim.updated_at.desc()
                ).limit(10).all()
                
                for claim in claims:
                    claims_list.append({
                        "id": claim.id,
                        "text": claim.claim_text[:200] + "..." if len(ev.claim_text or "") > 200 else ev.claim_text,
                        "claim_type": claim.claim_type,
                        "confidence": claim.confidence_level if hasattr(claim, 'confidence_level') else "unknown",
                        "timestamp": claim.updated_at.isoformat() if claim.updated_at else None,
                        "source": claim.source_location
                    })
        
        return claims_list

    def _get_graph_provenance(self, decision_id: str, decision_type: Optional[str]) -> List[Dict[str, Any]]:
        """Trace Care Graph nodes and edges that contributed to a decision."""
        graph_paths = []
        
        if decision_type == "CareRecommendation":
            # Get the recommendation
            recommendation = self.db.query(CareRecommendation).filter(
                CareRecommendation.id == decision_id
            ).first()
            
            if recommendation:
                person_id = recommendation.person_id
                # Get the person's current care graph subgraph
                nodes = self.db.query(CareGraphNode).filter(
                    CareGraphNode.person_id == person_id,
                    CareGraphNode.temporal_status == "CURRENT"  # Only current nodes
                ).all()
                
                node_ids = [n.id for n in nodes]
                edges = self.db.query(CareGraphEdge).filter(
                    or_(
                        CareGraphEdge.source_id.in_(node_ids),
                        CareGraphEdge.target_id.in_(node_ids)
                    ),
                    CareGraphEdge.temporal_status == "CURRENT"
                ).all()
                
                # Return simplified graph structure
                graph_paths.append({
                    "type": "care_graph_subgraph",
                    "person_id": person_id,
                    "node_count": len(nodes),
                    "edge_count": len(edges),
                    "nodes": [{"id": n.id, "label": getattr(n, 'label', None), "entity_type": getattr(n, 'entity_type', None)} for n in nodes[:10]],  # Limit to 10 for brevity
                    "edges": [{"id": e.id, "source": e.source_id, "target": e.target_id, "type": getattr(e, 'relationship_type', None)} for e in edges[:10]]
                })
                
                # Also look at recent changes (versions) to show what changed leading to this decision
                recent_node_versions = self.db.query(CareGraphNodeVersion).join(
                    CareGraphNode, CareGraphNodeVersion.node_id == CareGraphNode.id
                ).filter(
                    CareGraphNode.person_id == person_id
                ).order_by(
                    CareGraphNodeVersion.created_at.desc()
                ).limit(5).all()
                
                if recent_node_versions:
                    graph_paths.append({
                        "type": "recent_node_changes",
                        "changes": [
                            {
                                "node_id": v.node_id,
                                "change_type": v.change_type,
                                "timestamp": v.created_at.isoformat() if v.created_at else None,
                                "fields_changed": getattr(v, 'changed_fields', [])
                            } for v in recent_node_versions
                        ]
                    })
        
        return graph_paths

    def _estimate_evidence_confidence(self, evidence: Evidence) -> float:
        """Estimate confidence in evidence based on source type and other factors."""
        base_confidence = 0.5
        
        # Adjust based on source type
        source_confidence_map = {
            "DIRECT_OBSERVATION": 0.9,
            "MANUAL_CAREGIVER_INPUT": 0.7,
            "MEDICAL_RECORD": 0.85,
            "LAB_RESULT": 0.95,
            "SELF_REPORT": 0.6,
            "SENSOR_DATA": 0.8
        }
        
        if evidence.source_type in source_confidence_map:
            base_confidence = source_confidence_map[evidence.source_type]
        
        # Adjust based on recency (more recent = higher confidence)
        if evidence.event_time:
            hours_old = (datetime.now(timezone.utc) - evidence.event_time.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            if hours_old < 1:
                recency_factor = 1.0
            elif hours_old < 24:
                recency_factor = 0.9
            elif hours_old < 168:  # 1 week
                recency_factor = 0.7
            else:
                recency_factor = 0.5
            base_confidence *= recency_factor
        
        # Ensure confidence is in valid range
        return max(0.0, min(1.0, base_confidence))

    def _calculate_uncertainty(self, evidence_list: List[Dict[str, Any]], claims_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate uncertainty metrics based on evidence and claims quality."""
        if not evidence_list and not claims_list:
            return {
                "overall_confidence": 0.1,
                "confidence_level": "very_low",
                "sources": [],
                "warning": "No evidence or claims found for this decision"
            }
        
        # Calculate average evidence confidence
        evidence_confidences = [e.get("confidence", 0.5) for e in evidence_list]
        avg_evidence_confidence = sum(evidence_confidences) / len(evidence_confidences) if evidence_confidences else 0.5
        
        # Calculate claim-based confidence (simplified)
        claim_confidence = 0.7 if claims_list else 0.5  # Default moderate confidence if we have claims
        
        # Combine confidences (weighted average)
        if evidence_list and claims_list:
            overall_confidence = (avg_evidence_confidence * 0.7) + (claim_confidence * 0.3)
        elif evidence_list:
            overall_confidence = avg_evidence_confidence
        else:
            overall_confidence = claim_confidence
        
        # Determine confidence level
        if overall_confidence >= 0.8:
            confidence_level = "high"
        elif overall_confidence >= 0.6:
            confidence_level = "moderate"
        elif overall_confidence >= 0.4:
            confidence_level = "low"
        else:
            confidence_level = "very_low"
        
        # List sources
        sources = list(set([e.get("source_type", "unknown") for e in evidence_list if e.get("source_type")]))
        
        return {
            "overall_confidence": round(overall_confidence, 3),
            "confidence_level": confidence_level,
            "evidence_count": len(evidence_list),
            "claims_count": len(claims_list),
            "sources": sources,
            "evidence_quality": {
                "average_confidence": round(avg_evidence_confidence, 3) if evidence_list else 0,
                "confidence_std": self._calculate_std(evidence_confidences) if len(evidence_confidences) > 1 else 0
            }
        }

    def _calculate_std(self, values: List[float]) -> float:
        """Calculate standard deviation."""
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

    def attach_explanation(self, response: Dict[str, Any], decision_id: str) -> Dict[str, Any]:
        """Attach an explanation to an API response."""
        explanation = self.get_provenance_chain(decision_id)
        response["explanation"] = explanation
        return response


def get_explainability_service(db: Session) -> ExplainabilityService:
    """Dependency injection factory."""
    return ExplainabilityService(db)
