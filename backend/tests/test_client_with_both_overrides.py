
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models.identity import Account
from app.core.identity_enums import AccountType
from app.core.security import AuthenticatedPrincipal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base

# Create a shared in-memory database
engine = create_engine(
    'sqlite:///:memory:',
    connect_args={'check_same_thread': False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_test_db():
    try:
        yield SessionLocal()
    finally:
        SessionLocal().close()

def get_test_current_active_user():
    # Create a mock account
    mock_account = Account(id='test-account', is_active=True, account_type=AccountType.CAREGIVER)
    
    # Create a mock principal that allows access to any person
    class MockAuthenticatedPrincipal(AuthenticatedPrincipal):
        def has_access_to_person(self, person_id: str) -> bool:
            return True
    
    return MockAuthenticatedPrincipal(mock_account, [])

@pytest.fixture
def client():
    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_active_user] = get_test_current_active_user
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()

def test_client_with_both_overrides(client):
    response = client.get('/')
    assert response.status_code == 200

