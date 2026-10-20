
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.core.database import get_db
from app.core.security import get_current_active_user
from sqlalchemy.orm import Session

def test_testclient_direct():
    # Create a TestClient
    client = TestClient(app)
    
    # Override the dependencies
    def get_test_db():
        # We need to create a database session
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app.core.database import Base
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
    
    def get_test_current_active_user():
        from app.models.identity import Account
        from app.core.identity_enums import AccountType
        from app.core.security import AuthenticatedPrincipal
        
        # Create a mock account
        mock_account = Account(id='test-account', is_active=True, account_type=AccountType.CAREGIVER)
        
        # Create a mock principal that allows access to any person
        class MockAuthenticatedPrincipal(AuthenticatedPrincipal):
            def has_access_to_person(self, person_id: str) -> bool:
                return True
        
        return MockAuthenticatedPrincipal(mock_account, [])
    
    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_active_user] = get_test_current_active_user
    
    # Create a person and a caregiver
    from app.models.identity import Person
    from app.models.caregiver import Caregiver
    db = next(get_test_db())
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
    
    # Make a request to the test endpoint
    response = client.get(f'/graph/{person_id}/test')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'
    
    # Clean up
    app.dependency_overrides.clear()

