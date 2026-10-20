
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.models.identity import Person
from app.models.caregiver import Caregiver

@pytest.fixture
def db():
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)

def test_orm(db):
    person_id = 'test-person'
    other_person_id = 'test-other-person'
    caregiver_id = 'test-caregiver'
    
    person = Person(id=person_id, name='Test Person')
    other_person = Person(id=other_person_id, name='Other Person')
    caregiver = Caregiver(
        id=caregiver_id, 
        name='Test Caregiver', 
        email='test@example.com',
        relationship='daughter',
        person_id=person_id
    )
    
    db.add_all([person, other_person, caregiver])
    db.commit()
    
    # Query to make sure they are there
    q = db.query(Person).filter(Person.id == person_id).first()
    assert q is not None
    assert q.name == 'Test Person'

