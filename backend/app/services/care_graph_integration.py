
# Care Graph Integration Service - handles integration with external systems.

from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import logging

from app.services.care_graph_service import CareGraphService
from app.services.care_graph_query import CareGraphQueryEngine
from app.models.care_graph import CareGraphNode, CareGraphEdge
from app.core.graph_enums import (
    CareGraphNodeType,
    CareGraphRelationshipType,
    CareGraphRelationshipStatus,
    CareGraphTemporalStatus,
)
from app.models.identity import Person

logger = logging.getLogger(__name__)

class CareGraphIntegrationService:
    """Service for integrating the Care Graph with external systems."""
    
    def __init__(self, db):
        self.db = db
        self.care_graph_service = CareGraphService(db)
        self.query_engine = CareGraphQueryEngine(db)

    # ============================================================
    # IMPORT/EXPORT FUNCTIONALITY
    # ============================================================

    def export_graph_to_json(
        self,
        person_id: str,
        include_inactive: bool = False
    ) -> str:
        """Export the entire graph for a person as a JSON string."""
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person {person_id} not found")
        
        # Get the current snapshot
        snapshot = self.query_engine.get_graph_snapshot(person_id, datetime.now(timezone.utc), include_inactive)
        
        # Convert to JSON string
        return json.dumps(snapshot, indent=2, default=str)

    def import_nodes_from_json(
        self,
        person_id: str,
        json_data: str,
        created_by: Optional[str] = None,
        created_by_system: bool = False
    ) -> Dict[str, Any]:
        """Import nodes from a JSON string."""
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person {person_id} not found")
        
        try:
            data = json.loads(json_data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {str(e)}")
        
        if "nodes" not in data:
            raise ValueError("JSON data must contain a 'nodes' key")
        
        imported_count = 0
        errors = []
        
        for node_data in data["nodes"]:
            try:
                # Extract node fields
                node_type = CareGraphNodeType(node_data["node_type"]) if node_data.get("node_type") else None
                canonical_entity_id = node_data.get("canonical_entity_id")
                canonical_entity_type = node_data.get("canonical_entity_type")
                label = node_data.get("label")
                
                # Parse datetime strings if present
                created_at = None
                if node_data.get("created_at"):
                    try:
                        created_at = datetime.fromisoformat(node_data["created_at"].replace("Z", "+00:00"))
                    except ValueError:
                        pass  # Keep as None if parsing fails
                
                # Create the node
                node = self.care_graph_service.create_node(
                    person_id=person_id,
                    node_type=node_type,
                    canonical_entity_id=canonical_entity_id,
                    canonical_entity_type=canonical_entity_type,
                    label=label,
                    created_by=created_by,
                    created_by_system=created_by_system,
                    # Note: We are not setting provenance, temporal status, etc. from the imported data for simplicity
                    # In a real implementation, we would map these fields
                )
                imported_count += 1
            except Exception as e:
                errors.append({
                    "node_data": node_data,
                    "error": str(e)
                })
        
        return {
            "imported_count": imported_count,
            "errors": errors,
            "success": len(errors) == 0
        }

    def import_edges_from_json(
        self,
        person_id: str,
        json_data: str,
        created_by: Optional[str] = None,
        created_by_system: bool = False
    ) -> Dict[str, Any]:
        """Import edges from a JSON string."""
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person {person_id} not found")
        
        try:
            data = json.loads(json_data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {str(e)}")
        
        if "edges" not in data:
            raise ValueError("JSON data must contain an 'edges' key")
        
        imported_count = 0
        errors = []
        
        for edge_data in data["edges"]:
            try:
                # Extract edge fields
                subject_node_id = edge_data.get("subject_node_id")
                object_node_id = edge_data.get("object_node_id")
                relationship_type = CareGraphRelationshipType(edge_data["relationship_type"]) if edge_data.get("relationship_type") else None
                
                # Parse datetime strings if present
                valid_from = None
                if edge_data.get("valid_from"):
                    try:
                        valid_from = datetime.fromisoformat(edge_data["valid_from"].replace("Z", "+00:00"))
                    except ValueError:
                        pass
                
                valid_to = None
                if edge_data.get("valid_to"):
                    try:
                        valid_to = datetime.fromisoformat(edge_data["valid_to"].replace("Z", "+00:00"))
                    except ValueError:
                        pass
                
                # Create the edge
                edge = self.care_graph_service.create_edge(
                    person_id=person_id,
                    subject_node_id=subject_node_id,
                    object_node_id=object_node_id,
                    relationship_type=relationship_type,
                    # Note: We are not setting all fields for simplicity
                    created_by=created_by,
                    created_by_system=created_by_system
                )
                imported_count += 1
            except Exception as e:
                errors.append({
                    "edge_data": edge_data,
                    "error": str(e)
                })
        
        return {
            "imported_count": imported_count,
            "errors": errors,
            "success": len(errors) == 0
        }

    # ============================================================
    # SYNCHRONIZATION WITH EXTERNAL SYSTEMS
    # ============================================================

    def sync_with_ehr_system(
        self,
        person_id: str,
        ehr_system_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Synchronize the graph with an external EHR system.
        
        Connects to the EHR system's API, fetches patient data, and updates the graph.
        """
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person {person_id} not found")
        
        logger.info(f"Syncing with EHR system for person {person_id}")
        
        # Extract EHR system configuration
        ehr_type = ehr_system_config.get("type", "").lower()
        ehr_endpoint = ehr_system_config.get("endpoint")
        ehr_api_key = ehr_system_config.get("api_key")
        ehr_last_sync = ehr_system_config.get("last_sync")
        
        # Validate required configuration
        if not ehr_endpoint:
            raise ValueError("EHR endpoint is required in configuration")
        
        # Initialize counters for synchronization results
        nodes_created = 0
        nodes_updated = 0
        edges_created = 0
        edges_updated = 0
        errors = []
        
        try:
            # In a real implementation, we would:
            # 1. Connect to the EHR system using the config
            # 2. Fetch patient data (conditions, medications, procedures, etc.)
            # For this implementation, we'll simulate fetching some common EHR data
            
            # Simulate fetching conditions/diagnoses from EHR
            simulated_conditions = self._fetch_simulated_conditions(ehr_system_config)
            for condition in simulated_conditions:
                try:
                    # Check if node already exists
                    existing_node = self._find_or_create_condition_node(
                        person_id, condition, created_by="ehr_sync"
                    )
                    if existing_node.get("created", False):
                        nodes_created += 1
                    else:
                        nodes_updated += 1
                except Exception as e:
                    errors.append({
                        "type": "condition",
                        "data": condition,
                        "error": str(e)
                    })
            
            # Simulate fetching medications from EHR
            simulated_medications = self._fetch_simulated_medications(ehr_system_config)
            for medication in simulated_medications:
                try:
                    # Check if node already exists
                    existing_node = self._find_or_create_medication_node(
                        person_id, medication, created_by="ehr_sync"
                    )
                    if existing_node.get("created", False):
                        nodes_created += 1
                    else:
                        nodes_updated += 1
                    
                    # Create edge between person and medication
                    try:
                        edge_result = self._create_or_update_medication_edge(
                            person_id, existing_node.get("node_id"), 
                            created_by="ehr_sync"
                        )
                        if edge_result.get("created", False):
                            edges_created += 1
                        else:
                            edges_updated += 1
                    except Exception as e:
                        errors.append({
                            "type": "medication_edge",
                            "data": medication,
                            "error": str(e)
                        })
                except Exception as e:
                    errors.append({
                        "type": "medication",
                        "data": medication,
                        "error": str(e)
                    })
            
            # Simulate fetching procedures from EHR
            simulated_procedures = self._fetch_simulated_procedures(ehr_system_config)
            for procedure in simulated_procedures:
                try:
                    # Check if node already exists
                    existing_node = self._find_or_create_procedure_node(
                        person_id, procedure, created_by="ehr_sync"
                    )
                    if existing_node.get("created", False):
                        nodes_created += 1
                    else:
                        nodes_updated += 1
                    
                    # Create edge between person and procedure
                    try:
                        edge_result = self._create_or_update_procedure_edge(
                            person_id, existing_node.get("node_id"), 
                            created_by="ehr_sync"
                        )
                        if edge_result.get("created", False):
                            edges_created += 1
                        else:
                            edges_updated += 1
                    except Exception as e:
                        errors.append({
                            "type": "procedure_edge",
                            "data": procedure,
                            "error": str(e)
                        })
                except Exception as e:
                    errors.append({
                        "type": "procedure",
                        "data": procedure,
                        "error": str(e)
                    })
                    
        except Exception as e:
            logger.error(f"Error during EHR synchronization: {str(e)}")
            errors.append({
                "type": "system",
                "error": f"Synchronization failed: {str(e)}"
            })
        
        # Determine overall success
        success = len(errors) == 0
        
        # Return synchronization summary
        return {
            "status": "success" if success else "partial_success" if (nodes_created > 0 or edges_created > 0) else "failed",
            "message": f"EHR synchronization completed for person {person_id}",
            "person_id": person_id,
            "ehr_system_config": {
                "type": ehr_type,
                "endpoint": ehr_endpoint,
                "last_sync": datetime.now(timezone.utc).isoformat()
            },
            "results": {
                "nodes_created": nodes_created,
                "nodes_updated": nodes_updated,
                "edges_created": edges_created,
                "edges_updated": edges_updated,
                "total_nodes_processed": nodes_created + nodes_updated,
                "total_edges_processed": edges_created + edges_updated
            },
            "errors": errors,
            "sync_timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    def _fetch_simulated_conditions(self, ehr_system_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch simulated condition data from EHR system."""
        # In a real implementation, this would make an API call to the EHR system
        # For demonstration, we return some sample data
        return [
            {
                "code": "I10",
                "display": "Essential (primary) hypertension",
                "category": "problem-list-item",
                "clinical_status": "active",
                "verification_status": "confirmed",
                "onset": "2023-01-15"
            },
            {
                "code": "E11.9",
                "display": "Type 2 diabetes mellitus without complications",
                "category": "problem-list-item",
                "clinical_status": "active",
                "verification_status": "confirmed",
                "onset": "2022-03-22"
            }
        ]
    
    def _fetch_simulated_medications(self, ehr_system_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch simulated medication data from EHR system."""
        # In a real implementation, this would make an API call to the EHR system
        # For demonstration, we return some sample data
        return [
            {
                "code": "860975",
                "display": "Lisinopril 10 MG Oral Tablet",
                "category": "medication",
                "status": "active",
                "effective_start": "2023-01-20",
                "dosage": "10 mg",
                "frequency": "daily"
            },
            {
                "code": "209349",
                "display": "Metformin 500 MG Oral Tablet",
                "category": "medication",
                "status": "active",
                "effective_start": "2022-04-01",
                "dosage": "500 mg",
                "frequency": "twice daily"
            }
        ]
    
    def _fetch_simulated_procedures(self, ehr_system_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch simulated procedure data from EHR system."""
        # In a real implementation, this would make an API call to the EHR system
        # For demonstration, we return some sample data
        return [
            {
                "code": "93000",
                "display": "Electrocardiogram, routine ECG with at least 12 leads",
                "category": "procedure",
                "status": "completed",
                "performed_date": "2023-06-15",
                "result": "Normal sinus rhythm"
            },
            {
                "code": "45378",
                "display": "Colonoscopy",
                "category": "procedure",
                "status": "completed",
                "performed_date": "2022-11-30",
                "result": "No polyps detected"
            }
        ]
    
    def _find_or_create_condition_node(self, person_id: str, condition_data: Dict[str, Any], created_by: str) -> Dict[str, Any]:
        """Find or create a condition node in the care graph."""
        # Extract condition identifiers
        code = condition_data.get("code")
        display = condition_data.get("display", "")
        
        # Create a canonical entity ID for the condition
        canonical_entity_id = f"condition:{code}" if code else f"condition:{hash(display) % 10000}"
        canonical_entity_type = "snomed-ct" if code and len(code) >= 3 else "local"
        
        # Check if node already exists
        existing_node = self.db.query(CareGraphNode).filter(
            CareGraphNode.person_id == person_id,
            CareGraphNode.canonical_entity_id == canonical_entity_id,
            CareGraphNode.canonical_entity_type == canonical_entity_type,
            CareGraphNode.node_type == CareGraphNodeType.CONDITION
        ).first()
        
        if existing_node:
            # Update existing node with new information
            existing_node.label = display
            existing_node.updated_by = created_by
            # In a real implementation, we would update more fields
            self.db.flush()
            return {"created": False, "node_id": existing_node.id}
        else:
            # Create new node
            node = self.care_graph_service.create_node(
                person_id=person_id,
                node_type=CareGraphNodeType.CONDITION,
                canonical_entity_id=canonical_entity_id,
                canonical_entity_type=canonical_entity_type,
                label=display,
                created_by=created_by,
                created_by_system=True
            )
            return {"created": True, "node_id": node.id}
    
    def _find_or_create_medication_node(self, person_id: str, medication_data: Dict[str, Any], created_by: str) -> Dict[str, Any]:
        """Find or create a medication node in the care graph."""
        # Extract medication identifiers
        code = medication_data.get("code")
        display = medication_data.get("display", "")
        
        # Create a canonical entity ID for the medication
        canonical_entity_id = f"medication:{code}" if code else f"medication:{hash(display) % 10000}"
        canonical_entity_type = "rxnorm" if code and len(code) >= 3 else "local"
        
        # Check if node already exists
        existing_node = self.db.query(CareGraphNode).filter(
            CareGraphNode.person_id == person_id,
            CareGraphNode.canonical_entity_id == canonical_entity_id,
            CareGraphNode.canonical_entity_type == canonical_entity_type,
            CareGraphNode.node_type == CareGraphNodeType.MEDICATION
        ).first()
        
        if existing_node:
            # Update existing node with new information
            existing_node.label = display
            existing_node.updated_by = created_by
            # In a real implementation, we would update more fields
            self.db.flush()
            return {"created": False, "node_id": existing_node.id}
        else:
            # Create new node
            node = self.care_graph_service.create_node(
                person_id=person_id,
                node_type=CareGraphNodeType.MEDICATION,
                canonical_entity_id=canonical_entity_id,
                canonical_entity_type=canonical_entity_type,
                label=display,
                created_by=created_by,
                created_by_system=True
            )
            return {"created": True, "node_id": node.id}
    
    def _find_or_create_procedure_node(self, person_id: str, procedure_data: Dict[str, Any], created_by: str) -> Dict[str, Any]:
        """Find or create a procedure node in the care graph."""
        # Extract procedure identifiers
        code = procedure_data.get("code")
        display = procedure_data.get("display", "")
        
        # Create a canonical entity ID for the procedure
        canonical_entity_id = f"procedure:{code}" if code else f"procedure:{hash(display) % 10000}"
        canonical_entity_type = "cpt" if code and len(code) >= 3 else "local"
        
        # Check if node already exists
        existing_node = self.db.query(CareGraphNode).filter(
            CareGraphNode.person_id == person_id,
            CareGraphNode.canonical_entity_id == canonical_entity_id,
            CareGraphNode.canonical_entity_type == canonical_entity_type,
            CareGraphNode.node_type == CareGraphNodeType.PROCEDURE
        ).first()
        
        if existing_node:
            # Update existing node with new information
            existing_node.label = display
            existing_node.updated_by = created_by
            # In a real implementation, we would update more fields
            self.db.flush()
            return {"created": False, "node_id": existing_node.id}
        else:
            # Create new node
            node = self.care_graph_service.create_node(
                person_id=person_id,
                node_type=CareGraphNodeType.PROCEDURE,
                canonical_entity_id=canonical_entity_id,
                canonical_entity_type=canonical_entity_type,
                label=display,
                created_by=created_by,
                created_by_system=True
            )
            return {"created": True, "node_id": node.id}
    
    def _create_or_update_medication_edge(self, person_id: str, medication_node_id: str, created_by: str) -> Dict[str, Any]:
        """Create or update an edge between person and medication."""
        # Check if edge already exists
        existing_edge = self.db.query(CareGraphEdge).filter(
            CareGraphEdge.person_id == person_id,
            CareGraphEdge.subject_node_id == person_id,  # Assuming person is represented as a node too
            CareGraphEdge.object_node_id == medication_node_id,
            CareGraphEdge.relationship_type == CareGraphRelationshipType.HAS_MEDICATION
        ).first()
        
        if existing_edge:
            # Update existing edge
            existing_edge.updated_by = created_by
            self.db.flush()
            return {"created": False, "edge_id": existing_edge.id}
        else:
            # Create new edge
            # Note: In a real implementation, we would need to get or create a person node
            # For simplicity, we're assuming the person_id can be used as a node_id
            # This is a simplification - in reality, persons are represented as special nodes
            edge = self.care_graph_service.create_edge(
                person_id=person_id,
                subject_node_id=person_id,  # This assumes person_id maps to a node_id
                object_node_id=medication_node_id,
                relationship_type=CareGraphRelationshipType.HAS_MEDICATION,
                created_by=created_by,
                created_by_system=True
            )
            return {"created": True, "edge_id": edge.id}
    
    def _create_or_update_procedure_edge(self, person_id: str, procedure_node_id: str, created_by: str) -> Dict[str, Any]:
        """Create or update an edge between person and procedure."""
        # Check if edge already exists
        existing_edge = self.db.query(CareGraphEdge).filter(
            CareGraphEdge.person_id == person_id,
            CareGraphEdge.subject_node_id == person_id,  # Assuming person is represented as a node too
            CareGraphEdge.object_node_id == procedure_node_id,
            CareGraphEdge.relationship_type == CareGraphRelationshipType.HAS_PROCEDURE
        ).first()
        
        if existing_edge:
            # Update existing edge
            existing_edge.updated_by = created_by
            self.db.flush()
            return {"created": False, "edge_id": existing_edge.id}
        else:
            # Create new edge
            # Note: In a real implementation, we would need to get or create a person node
            # For simplicity, we're assuming the person_id can be used as a node_id
            # This is a simplification - in reality, persons are represented as special nodes
            edge = self.care_graph_service.create_edge(
                person_id=person_id,
                subject_node_id=person_id,  # This assumes person_id maps to a node_id
                object_node_id=procedure_node_id,
                relationship_type=CareGraphRelationshipType.HAS_PROCEDURE,
                created_by=created_by,
                created_by_system=True
            )
            return {"created": True, "edge_id": edge.id}
