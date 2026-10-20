"""Test for advanced causal inference service."""

from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock
from sqlalchemy.orm import Session
from app.models.care_graph import CareGraphNode, CareGraphEdge
from app.models.identity import Person
from app.services.causal_inference import CausalInferenceService


def test_causal_inference_service_initialization():
    """Test that the causal inference service initializes correctly."""
    mock_db = Mock(spec=Session)
    service = CausalInferenceService(mock_db)
    assert service.db == mock_db


def test_build_structural_causal_model():
    """Test building a structural causal model from a subgraph."""
    mock_db = Mock(spec=Session)
    service = CausalInferenceService(mock_db)
    
    # Create mock subgraph data
    subgraph = {
        "nodes": [
            {"id": "node1", "entity_type": "medication", "label": "Med A", 
             "properties": {"dosage": 10}, "temporal_status": "historical"},
            {"id": "node2", "entity_type": "symptom", "label": "Headache", 
             "properties": {"severity": 5}, "temporal_status": "current"},
            {"id": "node3", "entity_type": "lab_result", "label": "BP", 
             "properties": {"systolic": 120}, "temporal_status": "current"}
        ],
        "edges": [
            {"id": "edge1", "source_id": "node1", "target_id": "node2", 
             "relationship_type": "causes", "properties": {"strength": 0.8}},
            {"id": "edge2", "source_id": "node2", "target_id": "node3", 
             "relationship_type": "associated_with", "properties": {"strength": 0.5}}
        ]
    }
    
    scm = service._build_structural_causal_model(subgraph)
    
    # Check that the SCM was built correctly
    assert "nodes" in scm
    assert "edges" in scm
    assert "parents" in scm
    assert "children" in scm
    assert "confounders" in scm
    assert "mediators" in scm
    
    # Check parents and children relationships
    assert scm["parents"]["node2"] == ["node1"]
    assert scm["parents"]["node3"] == ["node2"]
    assert scm["children"]["node1"] == ["node2"]
    assert scm["children"]["node2"] == ["node3"]


def test_do_calculus_backdoor_adjustment():
    """Test the backdoor adjustment method."""
    mock_db = Mock(spec=Session)
    service = CausalInferenceService(mock_db)
    
    # Create mock subgraph data
    subgraph = {
        "nodes": [
            {"id": "treatment", "entity_type": "medication", "label": "Med A", 
             "properties": {"dosage": 10}},
            {"id": "outcome", "entity_type": "symptom", "label": "Headache", 
             "properties": {"severity": 5}},
            {"id": "confounder1", "entity_type": "characteristic", "label": "Age", 
             "properties": {"value": 65}},
            {"id": "confounder2", "entity_type": "characteristic", "label": "Gender", 
             "properties": {"value": "female"}}
        ],
        "edges": [
            {"id": "e1", "source_id": "confounder1", "target_id": "treatment", 
             "relationship_type": "influences"},
            {"id": "e2", "source_id": "confounder1", "target_id": "outcome", 
             "relationship_type": "influences"},
            {"id": "e3", "source_id": "confounder2", "target_id": "treatment", 
             "relationship_type": "influences"},
            {"id": "e4", "source_id": "confounder2", "target_id": "outcome", 
             "relationship_type": "influences"},
            {"id": "e5", "source_id": "treatment", "target_id": "outcome", 
             "relationship_type": "causes"}
        ]
    }
    
    result = service.do_calculus_backdoor_adjustment(
        subgraph, "treatment", "outcome"
    )
    
    # Check that we got a reasonable adjustment set
    assert "adjustment_set" in result
    assert result["method"] == "do-calculus backdoor adjustment"
    assert isinstance(result["adjustment_set"], list)


def test_counterfactual_simulation():
    """Test the counterfactual simulation method."""
    mock_db = Mock(spec=Session)
    
    # Mock the query methods
    mock_query = Mock()
    mock_db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [
        Mock(id="node1", node_type="medication", label="Med A", 
             properties={"dosage": 10}, temporal_status="historical"),
        Mock(node_type="symptom", label="Headache", 
             properties={"severity": 5}, temporal_status="current", id="node2")
    ]
    mock_query.filter.side_effect = lambda *args, **kwargs: mock_query
    
    service = CausalInferenceService(mock_db)
    
    # Define an intervention
    intervention = {
        "node1": {"dosage": 20}  # Increase dosage from 10 to 20
    }
    
    result = service.counterfactual_simulation("person1", intervention)
    
    # Check that we got a result
    assert "person_id" in result
    assert result["person_id"] == "person1"
    assert "intervention" in result
    assert result["intervention"] == intervention
    assert "causal_inference_details" in result
    assert result["causal_inference_details"]["method"] == "Structural causal model with do-calculus"
    assert "predicted_outcome" in result


def test_counterfactual_fairness_assessment():
    """Test the counterfactual fairness assessment method."""
    mock_db = Mock(spec=Session)
    
    # Mock the query methods for person lookup
    mock_person_query = Mock()
    mock_db.query.return_value = mock_person_query
    mock_person_query.filter.return_value = mock_person_query
    mock_person_query.first.return_value = Mock(
        id="person1",
        date_of_birth=datetime(1986, 6, 15, tzinfo=timezone.utc),
        name="Test Person"
    )
    
    # Mock the query methods for nodes and edges (used in get_person_subgraph)
    # We need to mock based on the model class being queried
    def query_side_effect(model):
        if model == Person:
            return mock_person_query
        elif model == CareGraphNode:
            mock_node_query = Mock()
            mock_node1 = Mock()
            mock_node1.id = "node1"
            mock_node1.node_type = "medication"
            mock_node1.label = "Med A"
            mock_node1.properties = {"dosage": 10}
            mock_node1.temporal_status = "historical"
            
            mock_node2 = Mock()
            mock_node2.id = "node2"
            mock_node2.node_type = "symptom"
            mock_node2.label = "Headache"
            mock_node2.properties = {"severity": 5}
            mock_node2.temporal_status = "current"
            
            mock_node_query.all.return_value = [mock_node1, mock_node2]
            mock_node_query.filter.return_value = mock_node_query
            return mock_node_query
        elif model == CareGraphEdge:
            mock_edge_query = Mock()
            mock_edge1 = Mock()
            mock_edge1.id = "edge1"
            mock_edge1.source_id = "node1"
            mock_edge1.target_id = "node2"
            mock_edge1.relationship_type = "causes"
            mock_edge1.properties = {"strength": 0.8}
            
            mock_edge_query.all.return_value = [mock_edge1]
            mock_edge_query.filter.return_value = mock_edge_query
            return mock_edge_query
        else:
            # Default mock for other models
            default_mock = Mock()
            default_mock.all.return_value = []
            default_mock.filter.return_value = default_mock
            return default_mock
    
    mock_db.query.side_effect = query_side_effect
    
    service = CausalInferenceService(mock_db)
    
    # Define an intervention
    intervention = {
        "node1": {"dosage": 20}  # Increase dosage from 10 to 20
    }
    
    result = service.counterfactual_fairness_assessment("person1", intervention)
    
    # Check that we got a result
    assert "person_id" in result
    assert result["person_id"] == "person1"
    assert "intervention" in result
    assert result["intervention"] == intervention
    assert "protected_attributes_assessed" in result
    assert "fairness_assessments" in result
    assert "overall_counterfactual_fair" in result
    assert isinstance(result["overall_counterfactual_fair"], bool)


def test_integrate_with_belief_updating():
    """Test integration with belief updating."""
    mock_db = Mock(spec=Session)
    
    # Mock the query methods for person lookup
    mock_person_query = Mock()
    mock_db.query.return_value = mock_person_query
    mock_person_query.filter.return_value = mock_person_query
    mock_person_query.first.return_value = Mock(
        id="person1",
        date_of_birth=datetime(1986, 6, 15, tzinfo=timezone.utc),
        name="Test Person"
    )
    
    # Mock the query methods for nodes and edges (used in get_person_subgraph)
    def query_side_effect(model):
        # Import the actual models to check against them
        from app.models.care_graph import CareGraphNode, CareGraphEdge
        from app.models.identity import Person
        
        if model == Person:
            return mock_person_query
        elif model == CareGraphNode:
            mock_node_query = Mock()
            mock_node1 = Mock()
            mock_node1.id = "node1"
            mock_node1.node_type = "medication"
            mock_node1.label = "Med A"
            mock_node1.properties = {"dosage": 10}
            mock_node1.temporal_status = "historical"
            
            mock_node2 = Mock()
            mock_node2.id = "node2"
            mock_node2.node_type = "symptom"
            mock_node2.label = "Headache"
            mock_node2.properties = {"severity": 5}
            mock_node2.temporal_status = "current"
            
            mock_node_query.all.return_value = [mock_node1, mock_node2]
            mock_node_query.filter.return_value = mock_node_query
            return mock_node_query
        elif model == CareGraphEdge:
            mock_edge_query = Mock()
            mock_edge1 = Mock()
            mock_edge1.id = "edge1"
            mock_edge1.source_id = "node1"
            mock_edge1.target_id = "node2"
            mock_edge1.relationship_type = "causes"
            mock_edge1.properties = {"strength": 0.8}
            
            mock_edge_query.all.return_value = [mock_edge1]
            mock_edge_query.filter.return_value = mock_edge_query
            return mock_edge_query
        else:
            # Default mock for other models
            default_mock = Mock()
            default_mock.all.return_value = []
            default_mock.filter.return_value = default_mock
            return default_mock
    
    mock_db.query.side_effect = query_side_effect
    
    service = CausalInferenceService(mock_db)
    
    # Define an intervention
    intervention = {
        "node1": {"dosage": 20}  # Increase dosage from 10 to 20
    }
    
    result = service.integrate_with_belief_updating("person1", intervention)
    
    # Check that we got a result with integration note
    assert "person_id" in result
    assert result["person_id"] == "person1"
    assert "integration_note" in result
    assert result["belief_integration_pending"] == True
    assert "causal_inference_details" in result


if __name__ == "__main__":
    test_causal_inference_service_initialization()
    test_build_structural_causal_model()
    test_do_calculus_backdoor_adjustment()
    test_counterfactual_simulation()
    test_counterfactual_fairness_assessment()
    test_integrate_with_belief_updating()
    print("All tests passed!")