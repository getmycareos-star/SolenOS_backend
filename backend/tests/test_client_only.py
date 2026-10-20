
import pytest
from app.models.identity import Person
from app.models.caregiver import Caregiver

def test_client_only(client, db):
    # Create a person and a caregiver
    person_id = 'test-person'
    caregiver_id = 'test-caregiver'
    
    person = Person(id=person_id, name='Test Person')
    caregiver = Caregiver(
        id=caregiver_id, 
        name='Test Caregiver', 
        email='test@example.com',
        relationship='daughter',
        person_id=person_id
    )
    
    db.add_all([person, caregiver])
    db.commit()
    
    # Override auth by patching the verify_person_access function
    from unittest.mock import patch
    with patch('app.api.care_graph.verify_person_access') as mock_verify:
        # Make the mock do nothing (allow access)
        mock_verify.return_value = None
        
        # Make a request to the test endpoint
        response = client.get(f'/graph/{person_id}/test')
        assert response.status_code == 200
        assert response.json()['status'] == 'ok'

