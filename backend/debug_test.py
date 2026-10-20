import sys
sys.path.insert(0, '.')

from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.database import Base
from app.main import app
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.schemas.situation import SituationInput

SQLALCHEMY_DATABASE_URL = 'sqlite:///:memory:'

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={'check_same_thread': False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

# Setup
Base.metadata.create_all(bind=engine)

try:
    from app.core.database import get_db
    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()
    person = Person(id='int-person-1', name='Test Person')
    caregiver = Caregiver(
        id='int-cg-1',
        name='Test Caregiver',
        email='int-test-1@example.com',
        relationship='Family',
        person_id=person.id,
        timezone='UTC',
    )
    db.add_all([person, caregiver])
    db.commit()
    db.close()

    client = TestClient(app)
    payload = SituationInput(
        caregiver_id='int-cg-1',
        person_id='int-person-1',
        raw_input='The person fell while getting out of bed.',
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    response = client.post('/api/situation', json=payload.model_dump(mode='json'))
    print('Status:', response.status_code)
    print('Response text:', response.text)
    if response.status_code != 200:
        print('Error details:')
        try:
            print(response.json())
        except:
            print('Could not parse JSON')
finally:
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()
