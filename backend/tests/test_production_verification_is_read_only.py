"""Regression guard: production verification must remain strictly read-only.

This test is static by design. It must never contact Railway or create test data.
All write/persistence checks belong in the disposable PostgreSQL integration job.
"""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "run_production_care_verification.py"
WORKFLOW = ROOT / ".github" / "workflows" / "production-care-verification.yml"


def test_production_verification_uses_only_get_requests():
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SCRIPT))

    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots <= {"__future__", "json", "os", "sys", "urllib"}, (
        "Production verification must not import application/database clients "
        "or libraries capable of bypassing the GET-only HTTP boundary."
    )

    request_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Request"
    ]
    assert len(request_calls) == 1, "Use one explicit HTTP request construction site."
    request = request_calls[0]
    keywords = {item.arg: item.value for item in request.keywords}
    assert "data" not in keywords, "Production verification must not send an HTTP request body."
    assert isinstance(keywords.get("method"), ast.Constant)
    assert keywords["method"].value == "GET", "Production verification may issue GET only."

    urlopen_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "urlopen"
    ]
    assert len(urlopen_calls) == 1
    assert len(urlopen_calls[0].args) == 1
    assert isinstance(urlopen_calls[0].args[0], ast.Name)
    assert urlopen_calls[0].args[0].id == "request"

    health_paths = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == "path":
            if isinstance(node.iter, ast.Tuple):
                health_paths = {
                    item.value for item in node.iter.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
    assert health_paths == {"/health", "/ready"}


def test_production_workflow_invokes_only_the_read_only_probe():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "run: python backend/scripts/run_production_care_verification.py" in workflow
    assert "sessions/anonymous" not in workflow
    assert "/api/v1/evidence" not in workflow
    assert "conversation/message" not in workflow
    assert "curl -X POST" not in workflow
