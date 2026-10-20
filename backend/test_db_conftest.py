import pytest
from app.models.identity import Person

def test_db(db):
    person = Person(id='test', name='Test Person')
    db.add(person)
    db.commit()
    assert person.id == 'test'
