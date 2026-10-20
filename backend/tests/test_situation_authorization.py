from unittest.mock import Mock

import pytest

from app.schemas.situation import SituationInput
from app.services.situation import process_situation_input


def test_rejects_caregiver_and_person_mismatch_before_writing():
    caregiver = Mock()
    caregiver.person_id = "person-owned-by-caregiver"
    caregiver.timezone = "UTC"

    person = Mock()
    person.id = "different-person"

    caregiver_query = Mock()
    caregiver_query.filter.return_value.first.return_value = caregiver
    person_query = Mock()
    person_query.filter.return_value.first.return_value = person

    db = Mock()
    db.query.side_effect = [caregiver_query, person_query]

    payload = SituationInput(
        caregiver_id="caregiver-1",
        person_id="different-person",
        raw_input="A care observation that must not be saved.",
    )

    with pytest.raises(ValueError, match="Caregiver is not linked to this person"):
        process_situation_input(db, payload)

    db.add.assert_not_called()
    db.flush.assert_not_called()
    db.commit.assert_not_called()
