import sys
import os
sys.path.insert(0, '.')

os.environ['LLM_ENABLED'] = 'false'

from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.schemas.situation import SituationInput
from app.models.evidence import Evidence
from app.models.care_event import CareEvent

SQLALCHEMY_DATABASE_URL = 'sqlite:///:memory:'

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={'check_same_thread': False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Setup
Base.metadata.create_all(bind=engine)

try:
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

    # Create evidence
    evidence = Evidence(
        person_id=person.id,
        type='note',
        source_text='The person fell while getting out of bed.',
        uploaded_by_caregiver_id=caregiver.id,
        confidence=None,
        evidence_status='reported',
        source_type='caregiver_observation',
    )
    db.add(evidence)
    db.flush()
    print('Evidence created: ', evidence.id)

    # Create care event
    care_event = CareEvent(
        person_id=person.id,
        event_type='observation',
        status='recorded',
        occurred_at=datetime.now(timezone.utc),
        title='The person fell while getting out of bed.',
        description='The person fell while getting out of bed.',
        evidence_ids=[evidence.id],
        created_by_caregiver_id=caregiver.id,
        reporter_ids=[caregiver.id],
    )
    db.add(care_event)
    db.flush()
    print('Care event created: ', care_event.id)

    db.commit()
finally:
    db.close()
    Base.metadata.drop_all(bind=engine)
