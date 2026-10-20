from datetime import datetime
from typing import Dict, List, Any
from sqlalchemy import or_


class CareGraphQueryEngine:
    def __init__(self, db):
        self.db = db

    def get_graph_snapshot(
        self, 
        person_id: str, 
        at_time: datetime, 
        include_inactive: bool = False
    ) -> Dict[str, List[Any]]:
        """Get the state of the graph at a specific point in time.
        
        Args:
            person_id: The person's ID
            at_time: The timestamp to query for
            include_inactive: Whether to include inactive/temporal status != CURRENT items
            
        Returns:
            Dictionary with "nodes" and "edges" lists containing node/edge dictionaries
        """
        from app.models.care_graph import CareGraphNode, CareGraphEdge
        from app.core.graph_enums import CareGraphTemporalStatus
        
        # Build base query conditions
        time_conditions = [
            CareGraphNode.person_id == person_id,
            CareGraphNode.valid_from <= at_time,
        ]
        
        if not include_inactive:
            time_conditions.append(
                or_(
                    CareGraphNode.valid_to.is_(None),
                    CareGraphNode.valid_to >= at_time
                )
            )
            time_conditions.append(
                CareGraphNode.temporal_status == CareGraphTemporalStatus.CURRENT
            )
        
        # Query nodes
        nodes = self.db.query(CareGraphNode).filter(*time_conditions).all()
        
        # Build edge conditions (similar to node conditions)
        edge_time_conditions = [
            CareGraphEdge.person_id == person_id,
            CareGraphEdge.valid_from <= at_time,
        ]
        
        if not include_inactive:
            edge_time_conditions.append(
                or_(
                    CareGraphEdge.valid_to.is_(None),
                    CareGraphEdge.valid_to >= at_time
                )
            )
            edge_time_conditions.append(
                CareGraphEdge.temporal_status == CareGraphTemporalStatus.CURRENT
            )
        
        # Query edges
        edges = self.db.query(CareGraphEdge).filter(*edge_time_conditions).all()
        
        # Convert to dictionaries
        return {
            "nodes": [self._node_to_dict(node) for node in nodes],
            "edges": [self._edge_to_dict(edge) for edge in edges],
        }

    def _node_to_dict(self, node) -> Dict[str, Any]:
        """Convert a node to a dictionary representation."""
        from app.core.graph_enums import CareGraphTemporalStatus
        
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
            "version": node.version,
            "created_at": node.created_at.isoformat() if node.created_at else None,
            "updated_at": node.updated_at.isoformat() if node.updated_at else None,
        }

    def _edge_to_dict(self, edge) -> Dict[str, Any]:
        """Convert an edge to a dictionary representation."""
        from app.core.graph_enums import CareGraphTemporalStatus
        
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
            "version": edge.version,
            "created_at": edge.created_at.isoformat() if edge.created_at else None,
            "updated_at": edge.updated_at.isoformat() if edge.updated_at else None,
        }
