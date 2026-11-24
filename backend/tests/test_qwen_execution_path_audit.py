"""Static guardrails for the single SolenOS Qwen execution gateway."""
import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"
GATEWAY_PATH = APP_ROOT / "services" / "qwen_gateway.py"


def test_ollama_native_chat_transport_exists_only_in_the_shared_gateway():
    offenders = []
    for source_path in APP_ROOT.rglob("*.py"):
        if source_path == GATEWAY_PATH:
            continue
        source = source_path.read_text(encoding="utf-8")
        if "/api/chat" in source or "OLLAMA_BASE_URL" in source:
            offenders.append(str(source_path.relative_to(BACKEND_ROOT)))
    assert offenders == []


def test_every_direct_qwen_chat_callsite_imports_the_shared_gateway():
    offenders = []
    for source_path in APP_ROOT.rglob("*.py"):
        if source_path == GATEWAY_PATH:
            continue
        source = source_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(source_path))
        except SyntaxError as exc:
            raise AssertionError(f"Unable to parse {source_path}: {exc}") from exc

        imported_gateway_symbols = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.services.qwen_gateway":
                imported_gateway_symbols.update(alias.asname or alias.name for alias in node.names)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "qwen_chat":
                if "qwen_chat" not in imported_gateway_symbols:
                    offenders.append(str(source_path.relative_to(BACKEND_ROOT)))
                    break
    assert offenders == []


def test_gateway_owns_the_only_native_ollama_chat_endpoint():
    source = GATEWAY_PATH.read_text(encoding="utf-8")
    assert 'base + "/api/chat"' in source
    assert "apply_specialized_intelligence(messages)" in source
    assert "apply_solenos_system_contract(messages)" in source
