"""Portable, assertion-based static checks for the care graph API."""
import ast
from pathlib import Path


CARE_GRAPH_PATH = Path(__file__).resolve().parents[1] / "app" / "api" / "care_graph.py"


def _source() -> str:
    assert CARE_GRAPH_PATH.is_file(), f"care graph source file not found: {CARE_GRAPH_PATH}"
    return CARE_GRAPH_PATH.read_text(encoding="utf-8")


def test_care_graph_api_syntax():
    """The actual source file must parse as valid Python."""
    ast.parse(_source(), filename=str(CARE_GRAPH_PATH))


def test_care_graph_api_routes():
    """The intended CRUD and snapshot routes must be declared."""
    content = _source()
    expected_routes = [
        '@router.post("/{person_id}/nodes"',
        '@router.get("/{person_id}/nodes/{node_id}"',
        '@router.put("/{person_id}/nodes/{node_id}"',
        '@router.delete("/{person_id}/nodes/{node_id}"',
        '@router.post("/{person_id}/edges"',
        '@router.get("/{person_id}/edges/{edge_id}"',
        '@router.put("/{person_id}/edges/{edge_id}"',
        '@router.delete("/{person_id}/edges/{edge_id}"',
        '@router.get("/{person_id}/snapshot"',
    ]
    missing_routes = [route for route in expected_routes if route not in content]
    assert not missing_routes, f"Missing care graph routes: {missing_routes}"


def test_care_graph_api_person_boundary():
    """Every graph read/write endpoint must use person-level authorization."""
    content = _source()
    assert "def verify_person_access" in content
    call_count = content.count("verify_person_access(person_id, current_user)")
    assert call_count >= 9, (
        "Expected authorization on 4 node routes, 4 edge routes and the snapshot "
        f"route; found {call_count} checks"
    )
