"""Regression tests for cross-dialect JSONList result decoding."""

from app.core.types import JSONList


def test_json_list_decodes_text_backed_json_values():
    processor = JSONList()
    assert processor.process_result_value('["sleep", "meals"]', None) == ["sleep", "meals"]
    assert processor.process_result_value(b'["sleep", "meals"]', None) == ["sleep", "meals"]


def test_json_list_accepts_native_postgresql_json_values():
    processor = JSONList()
    decoded_list = ["sleep", "meals"]
    decoded_object = {"status": "recorded"}

    assert processor.process_result_value(decoded_list, None) is decoded_list
    assert processor.process_result_value(decoded_object, None) is decoded_object


def test_json_list_preserves_null_values():
    assert JSONList().process_result_value(None, None) is None
