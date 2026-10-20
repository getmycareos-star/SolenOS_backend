"""Test for ensemble reasoning service."""

# This is a test comment to see if editing works
TEST_COMMENT_ADDED = True

from unittest.mock import Mock
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.services.ensemble_reasoning import EnsembleReasoningService, get_ensemble_reasoning_service
from app.models.behavior import BehaviorObservation


def test_ensemble_reasoning_service_initialization():
    """Test that the ensemble reasoning service initializes correctly."""
    mock_db = Mock(spec=Session)
    service = EnsembleReasoningService(mock_db)
    assert service.db == mock_db
    assert service.causal_service.db == mock_db
    assert service.belief_service.db == mock_db
    assert service.proactive_service.db == mock_db


def test_get_ensemble_reasoning_service():
    """Test the dependency injection factory."""
    mock_db = Mock(spec=Session)
    service = get_ensemble_reasoning_service(mock_db)
    assert isinstance(service, EnsembleReasoningService)
    assert service.db == mock_db


def test_temporal_reasoning_assessment():
    """Test temporal reasoning assessment."""
    mock_db = Mock(spec=Session)
    service = EnsembleReasoningService(mock_db)
    
    person_id = "person1"
    time_intervals = [
        {
            "start_time": "2026-10-09T08:00:00Z",
            "end_time": "2026-10-09T10:00:00Z",
            "label": "morning_medication"
        },
        {
            "start_time": "2026-10-09T10:00:00Z",
            "end_time": "2026-10-09T12:00:00Z",
            "label": "morning_activity"
        }
    ]
    
    result = service.temporal_reasoning_assessment(person_id, time_intervals)
    
    # Check that we got a result
    assert "person_id" in result
    assert result["person_id"] == person_id
    assert "temporal_analysis" in result
    assert result["temporal_analysis"]["intervals_analyzed"] == 2
    assert "assessment_timestamp" in result


def test_causal_reasoning_assessment():
    """Test causal reasoning assessment."""
    mock_db = Mock(spec=Session)
    
    # Mock the query methods for nodes and edges (used in get_person_subgraph)
    def query_side_effect(model):
        # Import the actual models to check against them
        from app.models.care_graph import CareGraphNode, CareGraphEdge
        from app.models.identity import Person
        
        if model == Person:
            mock_person_query = Mock()
            mock_person_query.filter.return_value = mock_person_query
            mock_person_query.first.return_value = Mock(
                id="person1",
                date_of_birth=datetime(1986, 6, 15, tzinfo=timezone.utc),
                name="Test Person"
            )
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
    
    service = EnsembleReasoningService(mock_db)
    
    person_id = "person1"
    intervention = {
        "node1": {"dosage": 20}  # Increase dosage from 10 to 20
    }
    
    result = service.causal_reasoning_assessment(person_id, intervention)
    
    # Check that we got a result
    assert "person_id" in result
    assert result["person_id"] == person_id
    assert "intervention" in result
    assert result["intervention"] == intervention
    assert "causal_inference_details" in result


def test_belief_reasoning_assessment():
    """Test belief reasoning assessment."""
    mock_db = Mock(spec=Session)
    
    # Mock the query methods
    mock_person_query = Mock()
    mock_belief_query = Mock()
    mock_evidence_query = Mock()
    mock_followup_query = Mock()
    mock_contradiction_query = Mock()
    mock_careplan_query = Mock()
    mock_engagementopportunity_query = Mock()
    
    def query_side_effect(model):
        from app.models.belief_state import BeliefState
        from app.models.evidence import Evidence
        from app.models.identity import Person
        from app.models.follow_up import FollowUp
        from app.models.uncertainty import Contradiction
        from app.models.care_planning import CarePlan
        from app.models.follow_up import EngagementOpportunity
        
        if model == Person:
            mock_person_query.filter.return_value = mock_person_query
            mock_person_query.first.return_value = Mock(id="person1")
            return mock_person_query
        elif model == BeliefState:
            mock_belief_query.filter.return_value = mock_belief_query
            mock_belief_query.order_by.return_value = mock_belief_query
            mock_belief_query.first.return_value = Mock()
            return mock_belief_query
        elif model == Evidence:
            mock_evidence_query.filter.return_value = mock_evidence_query
            mock_evidence_query.order_by.return_value = mock_evidence_query
            mock_evidence_query.all.return_value = []  # Return empty list for evidence queries
            return mock_evidence_query
        elif model == FollowUp:
            mock_followup_query.filter.return_value = mock_followup_query
            mock_followup_query.order_by.return_value = mock_followup_query
            mock_followup_query.limit.return_value = mock_followup_query
            mock_followup_query.all.return_value = []
            return mock_followup_query
        elif model == Contradiction:
            mock_contradiction_query.filter.return_value = mock_contradiction_query
            mock_contradiction_query.limit.return_value = mock_contradiction_query
            mock_contradiction_query.all.return_value = []
            return mock_contradiction_query
        elif model == CarePlan:
            mock_careplan_query.filter.return_value = mock_careplan_query
            mock_careplan_query.limit.return_value = mock_careplan_query
            mock_careplan_query.all.return_value = []
            return mock_careplan_query
        elif model == EngagementOpportunity:
            mock_engagementopportunity_query.filter.return_value = mock_engagementopportunity_query
            mock_engagementopportunity_query.all.return_value = []
            return mock_engagementopportunity_query
        else:
            default_mock = Mock()
            default_mock.all.return_value = []
            default_mock.filter.return_value = default_mock
            return default_mock
    
    mock_db.query.side_effect = query_side_effect
    
    service = EnsembleReasoningService(mock_db)
    
    person_id = "person1"
    evidence_list = [
        {
            "id": "ev1",
            "evidence_type": "observation",
            "description": "Symptom severity",
            "value": 7,
            "timestamp": "2026-10-09T14:00:00Z",
            "confidence": 0.9
        }
    ]
    
    result = service.belief_reasoning_assessment(person_id, evidence_list)
    
    # Check that we got a result
    assert isinstance(result, dict)


def test_active_reasoning_assessment():
    """Test active reasoning assessment."""
    from app.models.behavior import BehaviorObservation
    mock_db = Mock(spec=Session)
    
    # Mock the query methods
    mock_person_query = Mock()
    mock_belief_query = Mock()
    mock_evidence_query = Mock()
    mock_followup_query = Mock()
    mock_contradiction_query = Mock()
    mock_careplan_query = Mock()
    mock_engagementopportunity_query = Mock()
    
    def query_side_effect(model):
        from app.models.belief_state import BeliefState
        from app.models.evidence import Evidence
        from app.models.identity import Person
        from app.models.follow_up import FollowUp
        from app.models.uncertainty import Contradiction
        from app.models.care_planning import CarePlan
        from app.models.follow_up import EngagementOpportunity
        
        if model == Person:
            mock_person_query.filter.return_value = mock_person_query
            mock_person_query.first.return_value = Mock(id="person1")
            return mock_person_query
        elif model == BeliefState:
            mock_belief_query.filter.return_value = mock_belief_query
            mock_belief_query.order_by.return_value = mock_belief_query
            mock_belief_query.first.return_value = Mock()
            return mock_belief_query
        elif model == Evidence:
            mock_evidence_query.filter.return_value = mock_evidence_query
            mock_evidence_query.order_by.return_value = mock_evidence_query
            mock_evidence_query.limit.return_value = mock_evidence_query
            mock_evidence_query.all.return_value = []  # Return empty list for evidence queries
            return mock_evidence_query
        elif model == FollowUp:
            mock_followup_query.filter.return_value = mock_followup_query
            mock_followup_query.order_by.return_value = mock_followup_query
            mock_followup_query.limit.return_value = mock_followup_query
            mock_followup_query.all.return_value = []
            return mock_followup_query
        elif model == Contradiction:
            mock_contradiction_query.filter.return_value = mock_contradiction_query
            mock_contradiction_query.limit.return_value = mock_contradiction_query
            mock_contradiction_query.all.return_value = []
            return mock_contradiction_query
        elif model == CarePlan:
            mock_careplan_query.filter.return_value = mock_careplan_query
            mock_careplan_query.limit.return_value = mock_careplan_query
            mock_careplan_query.all.return_value = []
            return mock_careplan_query
        elif model == EngagementOpportunity:
            mock_engagementopportunity_query.filter.return_value = mock_engagementopportunity_query
            mock_engagementopportunity_query.all.return_value = []
            return mock_engagementopportunity_query
        elif model == BehaviorObservation:
            # Mock the BehaviorObservation query chain properly
            mock_behavior_query = Mock()
            mock_behavior_query.filter.return_value = mock_behavior_query
            mock_behavior_query.order_by.return_value = mock_behavior_query
            mock_behavior_query.nullslast.return_value = mock_behavior_query
            mock_behavior_query.limit.return_value = mock_behavior_query
            mock_behavior_query.all.return_value = [Mock(description="Test safety concern")]
            return mock_behavior_query
        else:
            default_mock = Mock()
            default_mock.all.return_value = []
            default_mock.filter.return_value = default_mock
            return default_mock
    
    mock_db.query.side_effect = query_side_effect
    
    service = EnsembleReasoningService(mock_db)
    
    # Mock the list_opportunities method to avoid iteration errors
    service.proactive_service.list_opportunities = Mock(return_value=[])
    
    person_id = "person1"
    
    result = service.active_reasoning_assessment(person_id)
    
    # Check that we got a result
    assert isinstance(result, dict)
    # The evaluate method should return specific keys
    assert "person_id" in result
    assert result["person_id"] == person_id
    assert "evaluated_at" in result
    assert "should_reach_out" in result


def test_ensemble_assessment():
    """Test ensemble assessment combining multiple reasoning approaches."""
    # TEST_COMMENT_ADDED
    # ANOTHER_TEST_COMMENT
    # YET_ANOTHER_COMMENT
    mock_db = Mock(spec=Session)
    
    # Mock all the necessary query methods
    def query_side_effect(model):
        from app.models.care_graph import CareGraphNode, CareGraphEdge
        from app.models.belief_state import BeliefState
        from app.models.evidence import Evidence
        from app.models.identity import Person
        from app.models.follow_up import FollowUp
        from app.models.uncertainty import Contradiction as ContradictionModel
        from app.models.follow_up import EngagementOpportunity
        from app.models.care_planning import CarePlan
        from app.models.behavior import BehaviorObservation
        
        if model == Person:
            mock_person_query = Mock()
            mock_person_query.filter.return_value = mock_person_query
            mock_person_query.first.return_value = Mock(
                id="person1",
                date_of_birth=datetime(1986, 6, 15, tzinfo=timezone.utc),
                name="Test Person"
            )
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
        elif model == BeliefState:
            mock_belief_query = Mock()
            mock_belief_query.filter.return_value = mock_belief_query
            mock_belief_query.order_by.return_value = mock_belief_query
            mock_belief_query.first.return_value = Mock()
            return mock_belief_query
        elif model == Evidence:
            mock_evidence_query = Mock()
            mock_evidence_query.filter.return_value = mock_evidence_query
            mock_evidence_query.order_by.return_value = mock_evidence_query
            mock_evidence_query.limit.return_value = mock_evidence_query
            mock_evidence_query.all.return_value = []
            return mock_evidence_query
        elif model == FollowUp:
            mock_followup_query = Mock()
            mock_followup_query.filter.return_value = mock_followup_query
            mock_followup_query.order_by.return_value = mock_followup_query
            mock_followup_query.limit.return_value = mock_followup_query
            mock_followup_query.all.return_value = []
            return mock_followup_query
        elif model == ContradictionModel:
            mock_contradiction_query = Mock()
            mock_contradiction_query.filter.return_value = mock_contradiction_query
            mock_contradiction_query.limit.return_value = mock_contradiction_query
            mock_contradiction_query.all.return_value = []
            return mock_contradiction_query
        elif model == EngagementOpportunity:
            mock_engagementopportunity_query = Mock()
            mock_engagementopportunity_query.filter.return_value = mock_engagementopportunity_query
            mock_engagementopportunity_query.all.return_value = []
            return mock_engagementopportunity_query
        elif model == BehaviorObservation:
            # Mock the BehaviorObservation query chain properly
            mock_behavior_query = Mock()
            mock_behavior_query.filter.return_value = mock_behavior_query
            mock_behavior_query.order_by.return_value = mock_behavior_query
            mock_behavior_query.nullslast.return_value = mock_behavior_query
            mock_behavior_query.limit.return_value = mock_behavior_query
            mock_behavior_query.all.return_value = [Mock(description="Test safety concern")]
            return mock_behavior_query
        elif model == CarePlan:
            mock_careplan_query = Mock()
            mock_careplan_query.filter.return_value = mock_careplan_query
            mock_careplan_query.limit.return_value = mock_careplan_query
            mock_careplan_query.all.return_value = []
            return mock_careplan_query
        else:
            default_mock = Mock()
            default_mock.all.return_value = []
            default_mock.filter.return_value = default_mock
            return default_mock
    
    mock_db.query.side_effect = query_side_effect
    
    service = EnsembleReasoningService(mock_db)
    
    # Mock the list_opportunities method to avoid iteration errors
    service.proactive_service.list_opportunities = Mock(return_value=[])
    
    person_id = "person1"
    temporal_intervals = [
        {
            "start_time": "2026-10-09T08:00:00Z",
            "end_time": "2026-10-09T10:00:00Z",
            "label": "morning_medication"
        }
    ]
    intervention = {
        "node1": {"dosage": 20}
    }
    evidence_list = [
        {
            "id": "ev1",
            "evidence_type": "observation",
            "description": "Symptom severity",
            "value": 7,
            "timestamp": "2026-10-09T14:00:00Z",
            "confidence": 0.9
        }
    ]
    
    result = service.ensemble_assessment(
        person_id=person_id,
        temporal_intervals=temporal_intervals,
        intervention=intervention,
        evidence_list=evidence_list
    )
    
    # Check that we got a result
    assert "person_id" in result
    assert result["person_id"] == person_id
    assert "ensemble_assessment" in result
    assert "assessment_timestamp" in result
    assert "reasoning_modules_used" in result
    
    # Check that all reasoning modules were used
    modules_used = result["reasoning_modules_used"]
    expected_modules = ["temporal", "causal", "belief", "active"]
    for module in expected_modules:
        assert module in modules_used, f"Module {module} not found in {modules_used}"
    
    # Check ensemble assessment structure
    ensemble = result["ensemble_assessment"]
    assert "temporal_reasoning" in ensemble
    assert "causal_reasoning" in ensemble
    assert "belief_reasoning" in ensemble
    assert "active_reasoning" in ensemble
    assert "combined_insights" in ensemble
    assert "ensemble_confidence" in ensemble
    
    # Check that confidence is a reasonable value
    confidence = ensemble["ensemble_confidence"]
    assert isinstance(confidence, float)
    assert 0.0 <= confidence <= 1.0


if __name__ == "__main__":
    test_ensemble_reasoning_service_initialization()
    test_get_ensemble_reasoning_service()
    test_temporal_reasoning_assessment()
    test_causal_reasoning_assessment()
    test_belief_reasoning_assessment()
    test_active_reasoning_assessment()
    test_ensemble_assessment()
    print("All tests passed!")