
import pytest
from app.models.identity import Person
from app.models.caregiver import Caregiver

def test_care_graph_db_only(db):
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
    
    # Query to make sure they are there
    q = db.query(Person).filter(Person.id == person_id).first()
    assert q is not None
    assert q.name == 'Test Person'

