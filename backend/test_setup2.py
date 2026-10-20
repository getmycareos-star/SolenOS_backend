import sys
import os
sys.path.insert(0, '.')

os.environ['LLM_ENABLED'] = 'false'

print('Importing app modules...')
from app.core.database import Base
from app.main import app
print('App modules imported.')

print('Creating engine...')
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
SQLALCHEMY_DATABASE_URL = 'sqlite:///:memory:'
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={'check_same_thread': False},
    poolclass=StaticPool,
)
print('Engine created.')

print('Creating sessionmaker...')
from sqlalchemy.orm import sessionmaker
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
print('Sessionmaker created.')

print('Creating all tables...')
Base.metadata.create_all(bind=engine)
print('Tables created.')

print('Done.')
