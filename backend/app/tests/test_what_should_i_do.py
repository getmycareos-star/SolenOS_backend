"""Tests for the what_should_i_do service."""

from unittest.mock import Mock, MagicMock
from sqlalchemy.orm import Session
from app.services.what_should_i_do_service import WhatShouldIDoService


def test_what_should_i_do_service_init():
    """Test that the service can be initialized."""
    mock_db = Mock(spec=Session)
    service = WhatShouldIDoService(mock_db)
    assert service.db == mock_db
    print(">> Service initialization test passed")


if __name__ == "__main__":
    test_what_should_i_do_service_init()
    print(">> All tests passed!")