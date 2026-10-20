import sys
import os
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
from app.services.situation import process_situation_input

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

    # Get a session from the override, just like the app does
    db_gen = override_get_db()
    db = next(db_gen)
    try:
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
    finally:
        # We won't close the db yet because we need it for the function call
        pass

    payload = SituationInput(
        caregiver_id='int-cg-1',
        person_id='int-person-1',
        raw_input='The person fell while getting out of bed.',
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    response = process_situation_input(db, payload)
    print("Response:", response)
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    try:
        db.close()
    except:
        pass
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()
