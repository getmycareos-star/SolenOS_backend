
# Care Graph Evolution Service - handles graph evolution over time.

from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import json

from app.models.care_graph import (
    CareGraphNode,
    CareGraphEdge,
    CareGraphNodeVersion,
    CareGraphEdgeVersion,
    CareGraphChangeLog,
    CareGraphGroup,
    CareGraphContradiction,
)
from app.core.graph_enums import (
    CareGraphNodeType,
    CareGraphRelationshipType,
    CareGraphRelationshipStatus,
    CareGraphTemporalStatus,
    CareGraphChangeType,
)
from app.models.identity import Person
from sqlalchemy import and_, or_
import logging

logger = logging.getLogger(__name__)

class CareGraphEvolutionService:
    """Service for managing Care Graph evolution over time."""
    
    def __init__(self, db):
        self.db = db

    # ============================================================
    # TEMPORAL QUERIES
    # ============================================================
    
    def get_graph_at_time(
        self,
        person_id: str,
        at_time: datetime,
        include_inactive: bool = False
    ) -> Dict[str, List[Any]]:
        """Get the state of the graph at a specific point in time."""
        # Reuse the query engine's snapshot functionality
        from app.services.care_graph_query import CareGraphQueryEngine
        query_engine = CareGraphQueryEngine(self.db)
        return query_engine.get_graph_snapshot(person_id, at_time, include_inactive)

    def get_node_evolution(
        self,
        person_id: str,
        node_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Get the evolution of a node over time."""
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person {person_id} not found")
        
        # Verify node belongs to person
        node = self.db.query(CareGraphNode).filter(
            CareGraphNode.id == node_id,
            CareGraphNode.person_id == person_id
        ).first()
        if not node:
            return []
        
        # Get versions in time range
        query = self.db.query(CareGraphNodeVersion).filter(
            CareGraphNodeVersion.node_id == node_id
        )
        
        if start_time:
            query = query.filter(CareGraphNodeVersion.created_at >= start_time)
        if end_time:
            query = query.filter(CareGraphNodeVersion.created_at <= end_time)
            
        versions = query.order_by(CareGraphNodeVersion.created_at.asc()).all()
        
        # Format for output
        return [{
            "version": v.version,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "change_type": v.change_type.value if v.change_type else None,
            "change_reason": v.change_reason,
            "modified_by": v.changed_by,
            "node_state": {
                "label": v.label,
                "node_type": v.node_type.value if v.node_type else None,
                "temporal_status": v.temporal_status.value if v.temporal_status else None,
                "valid_from": v.valid_from.isoformat() if v.valid_from else None,
                "valid_to": v.valid_to.isoformat() if v.valid_to else None,
            }
        } for v in versions]

    def get_edge_evolution(
        self,
        person_id: str,
        edge_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Get the evolution of an edge over time."""
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person {person_id} not found")
        
        # Verify edge belongs to person
        edge = self.db.query(CareGraphEdge).filter(
            CareGraphEdge.id == edge_id,
            CareGraphEdge.person_id == person_id
        ).first()
        if not edge:
            return []
        
        # Get versions in time range
        query = self.db.query(CareGraphEdgeVersion).filter(
            CareGraphEdgeVersion.edge_id == edge_id
        )
        
        if start_time:
            query = query.filter(CareGraphEdgeVersion.created_at >= start_time)
        if end_time:
            query = query.filter(CareGraphEdgeVersion.created_at <= end_time)
            
        versions = query.order_by(CareGraphEdgeVersion.created_at.asc()).all()
        
        # Format for output
        return [{
            "version": v.version,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "change_type": v.change_type.value if v.change_type else None,
            "change_reason": v.change_reason,
            "modified_by": v.changed_by,
            "edge_state": {
                "relationship_type": v.relationship_type.value if v.relationship_type else None,
                "status": v.status.value if v.status else None,
                "temporal_status": v.temporal_status.value if v.temporal_status else None,
                "valid_from": v.valid_from.isoformat() if v.valid_from else None,
                "valid_to": v.valid_to.isoformat() if v.valid_to else None,
                "confidence_existence": v.confidence_existence,
                "confidence_overall": v.confidence_overall
            }
        } for v in versions]

    # ============================================================
    # EVOLUTION ANALYTICS
    # ============================================================

    def get_relationship_stability(
        self,
        person_id: str,
        edge_id: str,
        time_window_days: int = 30
    ) -> Dict[str, Any]:
        """Calculate the stability of a relationship over a time window."""
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person {person_id} not found")
        
        # Verify edge belongs to person
        edge = self.db.query(CareGraphEdge).filter(
            CareGraphEdge.id == edge_id,
            CareGraphEdge.person_id == person_id
        ).first()
        if not edge:
            return {"stability_score": 0.0, "changes": 0, "time_window_days": time_window_days}
        
        # Get changes in the time window
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=time_window_days)
        
        changes = self.db.query(CareGraphChangeLog).filter(
            and_(
                CareGraphChangeLog.edge_id == edge_id,
                CareGraphChangeLog.created_at >= cutoff_time
            )
        ).count()
        
        # Calculate stability score (0-1, where 1 is completely stable)
        # Fewer changes = higher stability
        stability_score = max(0.0, 1.0 - (changes / 10.0))  # Normalize assuming 10+ changes = unstable
        
        return {
            "stability_score": min(1.0, stability_score),
            "changes": changes,
            "time_window_days": time_window_days,
            "latest_status": edge.status.value if edge.status else None
        }

    def get_node_lifespan(
        self,
        person_id: str,
        node_id: str
    ) -> Dict[str, Any]:
        """Calculate the lifespan of a node."""
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person {person_id} not found")
        
        # Verify node belongs to person
        node = self.db.query(CareGraphNode).filter(
            CareGraphNode.id == node_id,
            CareGraphNode.person_id == person_id
        ).first()
        if not node:
            return {"lifespan_days": 0, "is_active": False}
        
        # Calculate lifespan
        created_time = node.created_at
        if node.valid_to and node.temporal_status == CareGraphTemporalStatus.HISTORICAL:
            end_time = node.valid_to
            is_active = False
        else:
            end_time = datetime.now(timezone.utc)
            is_active = node.temporal_status == CareGraphTemporalStatus.CURRENT
        
        lifespan_days = (end_time - created_time).days
        
        return {
            "lifespan_days": max(0, lifespan_days),
            "is_active": is_active,
            "created_at": created_time.isoformat() if created_time else None,
            "ended_at": end_time.isoformat() if end_time else None,
            "temporal_status": node.temporal_status.value if node.temporal_status else None
        }

    def get_evolution_summary(
        self,
        person_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Get a summary of graph evolution over a time period."""
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person {person_id} not found")
        
        # Set default time range if not provided
        if not end_time:
            end_time = datetime.now(timezone.utc)
        if not start_time:
            start_time = end_time - timedelta(days=30)  # Last 30 days by default
        
        # Count nodes and edges created in time range
        nodes_created = self.db.query(CareGraphNode).filter(
            and_(
                CareGraphNode.person_id == person_id,
                CareGraphNode.created_at >= start_time,
                CareGraphNode.created_at <= end_time
            )
        ).count()
        
        edges_created = self.db.query(CareGraphEdge).filter(
            and_(
                CareGraphEdge.person_id == person_id,
                CareGraphEdge.created_at >= start_time,
                CareGraphEdge.created_at <= end_time
            )
        ).count()
        
        # Count changes in time range
        changes = self.db.query(CareGraphChangeLog).join(
            CareGraphNode, CareGraphChangeLog.node_id == CareGraphNode.id, isouter=True
        ).join(
            CareGraphEdge, CareGraphChangeLog.edge_id == CareGraphEdge.id, isouter=True
        ).filter(
            and_(
                or_(
                    CareGraphNode.person_id == person_id,
                    CareGraphEdge.person_id == person_id
                ),
                CareGraphChangeLog.created_at >= start_time,
                CareGraphChangeLog.created_at <= end_time
            )
        ).count()
        
        # Count by change type
        change_type_counts = {}
        for change_type in CareGraphChangeType:
            count = self.db.query(CareGraphChangeLog).join(
                CareGraphNode, CareGraphChangeLog.node_id == CareGraphNode.id, isouter=True
            ).join(
                CareGraphEdge, CareGraphChangeLog.edge_id == CareGraphEdge.id, isouter=True
            ).filter(
                and_(
                    or_(
                        CareGraphNode.person_id == person_id,
                        CareGraphEdge.person_id == person_id
                    ),
                    CareGraphChangeLog.change_type == change_type,
                    CareGraphChangeLog.created_at >= start_time,
                    CareGraphChangeLog.created_at <= end_time
                )
            ).count()
            if count > 0:
                change_type_counts[change_type.value] = count
        
        return {
            "time_period": {
                "start": start_time.isoformat() if start_time else None,
                "end": end_time.isoformat() if end_time else None
            },
            "nodes_created": nodes_created,
            "edges_created": edges_created,
            "total_changes": changes,
            "change_type_breakdown": change_type_counts,
            "current_node_count": self.db.query(CareGraphNode).filter(
                CareGraphNode.person_id == person_id
            ).count(),
            "current_edge_count": self.db.query(CareGraphEdge).filter(
                CareGraphEdge.person_id == person_id
            ).count()
        }

    # ============================================================
    # PREDICTION AND FORECASTING
    # ============================================================

    def predict_node_likelihood_of_change(
        self,
        person_id: str,
        node_id: str,
        days_ahead: int = 7
    ) -> Dict[str, Any]:
        """Predict the likelihood of a node changing in the near future."""
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person {person_id} not found")
        
        # Verify node belongs to person
        node = self.db.query(CareGraphNode).filter(
            CareGraphNode.id == node_id,
            CareGraphNode.person_id == person_id
        ).first()
        if not node:
            return {"likelihood": 0.0, "reason": "Node not found"}
        
        # Get recent change frequency for this node
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=30)
        recent_changes = self.db.query(CareGraphChangeLog).filter(
            and_(
                CareGraphChangeLog.node_id == node_id,
                CareGraphChangeLog.created_at >= cutoff_time
            )
        ).count()
        
        # Simple prediction based on recent activity
        # More recent changes = higher likelihood of future changes
        if recent_changes == 0:
            likelihood = 0.1  # Low baseline likelihood
        elif recent_changes <= 2:
            likelihood = 0.3  # Moderate likelihood
        else:
            likelihood = min(0.9, 0.1 + (recent_changes * 0.1))  # Increases with activity
        
        # Adjust based on node properties
        if node.temporal_status == CareGraphTemporalStatus.HISTORICAL:
            likelihood *= 0.1  # Historical nodes are less likely to change
        elif node.temporal_status == CareGraphTemporalStatus.FUTURE_PLANNED:
            likelihood *= 1.5  # Future planned nodes may be more active
        
        # Adjust based on node type
        high_volatility_types = {
            CareGraphNodeType.SYMPTOM,
            CareGraphNodeType.BEHAVIOURAL_STATE,
            CareGraphNodeType.FUNCTIONAL_STATE
        }
        if node.node_type in high_volatility_types:
            likelihood *= 1.3
        
        likelihood = min(1.0, likelihood)  # Cap at 1.0
        
        return {
            "likelihood": likelihood,
            "days_ahead": days_ahead,
            "recent_changes_30d": recent_changes,
            "factors": {
                "node_type": node.node_type.value if node.node_type else None,
                "temporal_status": node.temporal_status.value if node.temporal_status else None,
                "recent_activity_level": "high" if recent_changes > 2 else "medium" if recent_changes > 0 else "low"
            }
        }
