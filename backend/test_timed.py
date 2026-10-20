import sys
import os
import time
sys.path.insert(0, '.')

# Set environment variable to disable local model before importing any app modules
os.environ['LLM_ENABLED'] = 'false'

from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.schemas.situation import SituationInput
from fastapi.testclient import TestClient

# Define the engine and TestingSessionLocal as in conftest.py
SQLALCHEMY_DATABASE_URL = 'sqlite:///:memory:'

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={'check_same_thread': False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def setup_db():
    print('[' + time.strftime('%H:%M:%S') + '] setup_db start')
    Base.metadata.create_all(bind=engine)
    print('[' + time.strftime('%H:%M:%S') + '] setup_db end')

def teardown_db():
    print('[' + time.strftime('%H:%M:%S') + '] teardown_db start')
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()
    print('[' + time.strftime('%H:%M:%S') + '] teardown_db end')

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

# Now run the test exactly as in the test file
setup_db()
try:
    print('[' + time.strftime('%H:%M:%S') + '] Setting override')
    from app.core.database import get_db
    app.dependency_overrides[get_db] = override_get_db

    print('[' + time.strftime('%H:%M:%S') + '] Creating person and caregiver')
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
    print('[' + time.strftime('%H:%M:%S') + '] Person and caregiver created')

    print('[' + time.strftime('%H:%M:%S') + '] Creating TestClient')
    client = TestClient(app)
    print('[' + time.strftime('%H:%M:%S') + '] TestClient created')

    print('[' + time.strftime('%H:%M:%S') + '] Creating payload')
    payload = SituationInput(
        caregiver_id='int-cg-1',
        person_id='int-person-1',
        raw_input='The person fell while getting out of bed.',
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    print('[' + time.strftime('%H:%M:%S') + '] Payload created')

    print('[' + time.strftime('%H:%M:%S') + '] Making POST request')
    start = time.time()
    response = client.post('/api/situation', json=payload.model_dump(mode='json'))
    end = time.time()
    print('[' + time.strftime('%H:%M:%S') + '] POST request completed in', end - start, 'seconds')
    print('Status:', response.status_code)
    print('Response:', response.text)
finally:
    print('[' + time.strftime('%H:%M:%S') + '] teardown_db start')
    teardown_db()
    print('[' + time.strftime('%H:%M:%S') + '] teardown_db end')
