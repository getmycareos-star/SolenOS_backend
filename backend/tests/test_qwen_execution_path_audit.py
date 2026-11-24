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


def test_gateway_commits_a_privacy_minimizing_audit_receipt_before_model_inference():
    source = GATEWAY_PATH.read_text(encoding="utf-8")
    assert "AIInferenceMetric" in source
    assert "SOLENOS_OPERATING_CONTRACT_VERSION" in source
    assert "def _inference_audit_metadata" in source
    assert "def _begin_inference_metric" in source
    assert "def _complete_inference_metric" in source
    assert "audit_id = _begin_inference_metric(model, audit_metadata)" in source
    assert source.index("audit_id = _begin_inference_metric(model, audit_metadata)") < source.index("urllib.request.urlopen(request")
    assert "inference_audit_unavailable" in source
    assert '"system_prompt_sha256"' in source
    assert '"request_context_sha256"' in source
    assert "prompt contents" not in source.lower()


def test_delta_engine_uses_the_shared_gateway_and_its_longitudinal_specialist():
    source = (APP_ROOT / "services" / "delta_engine.py").read_text(encoding="utf-8")
    assert "from app.services.qwen_gateway import get_ollama_config, qwen_chat" in source
    assert "SOLENOS_WORKFLOW_ID: delta_engine." in source
    assert "_request_delta_json(" in source
    assert "base + \"/api/chat\"" not in source


def _dotted_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def test_qwen_chat_calls_and_references_are_gateway_imports_even_when_aliased():
    offenders = []
    discovered_call_sites = set()
    gateway_module = "app.services.qwen_gateway"
    for source_path in APP_ROOT.rglob("*.py"):
        if source_path == GATEWAY_PATH:
            continue
        relative = str(source_path.relative_to(BACKEND_ROOT))
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=relative)
        imported_chat_names = set()
        imported_gateway_module_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "qwen_chat":
                        if node.module != gateway_module:
                            offenders.append(f"{relative}: qwen_chat imported from {node.module}")
                        else:
                            imported_chat_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == gateway_module:
                        imported_gateway_module_names.add(alias.asname or alias.name.split(".")[0])

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _dotted_name(node.func)
            if target in imported_chat_names:
                discovered_call_sites.add(relative)
            elif target.endswith(".qwen_chat") and any(
                target == "{}.qwen_chat".format(module_name)
                or target == "{}.services.qwen_gateway.qwen_chat".format(module_name)
                for module_name in imported_gateway_module_names
            ):
                discovered_call_sites.add(relative)
            elif target.endswith(".qwen_chat"):
                offenders.append(f"{relative}: unverified qwen_chat receiver {target}")
            elif target == "qwen_chat":
                offenders.append(f"{relative}: qwen_chat called without a gateway import")

        # Passing qwen_chat as a callback (for example asyncio.to_thread) is
        # also an inference path, even though it is not itself a Call node.
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in imported_chat_names:
                discovered_call_sites.add(relative)

    expected_call_sites = {
        "app/api/collaboration.py",
        "app/api/handoff_drafts.py",
        "app/api/daily_care_intelligence.py",
        "app/services/delta_engine.py",
        "app/services/qwen_background_worker.py",
    }
    assert discovered_call_sites == expected_call_sites, {
        "discovered": sorted(discovered_call_sites),
        "expected": sorted(expected_call_sites),
    }
    assert offenders == [], offenders


def test_gateway_extraction_entry_points_delegate_to_the_single_qwen_chat():
    tree = ast.parse(GATEWAY_PATH.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in ("qwen_chat", "extract_review_candidates", "extract_companion_care_candidates"):
        assert name in functions, f"Missing gateway entry point: {name}"
    for name in ("extract_review_candidates", "extract_companion_care_candidates"):
        calls_gateway = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "qwen_chat"
            for node in ast.walk(functions[name])
        )
        assert calls_gateway, f"{name} must delegate inference to qwen_chat"


def test_gateway_extraction_consumers_are_explicitly_imported_from_the_gateway():
    expected = {
        "app/api/v1.py": {"extract_review_candidates"},
        "app/api/document_intelligence.py": {"extract_review_candidates"},
        "app/services/qwen_background_worker.py": {"extract_review_candidates"},
        "app/services/companion_care_updates.py": {"extract_companion_care_candidates"},
    }
    for relative, required_symbols in expected.items():
        tree = ast.parse((BACKEND_ROOT / relative).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.services.qwen_gateway":
                imported.update(alias.name for alias in node.names)
        assert required_symbols.issubset(imported), {
            "file": relative,
            "missing": sorted(required_symbols - imported),
        }


def test_no_non_gateway_ollama_sdk_or_native_inference_endpoint_exists():
    offenders = []
    forbidden_endpoints = (
        "/api/chat",
        "/api/generate",
        "/api/embed",
        "/api/embeddings",
    )
    for source_path in APP_ROOT.rglob("*.py"):
        if source_path == GATEWAY_PATH:
            continue
        relative = str(source_path.relative_to(BACKEND_ROOT))
        source = source_path.read_text(encoding="utf-8")
        if any(endpoint in source for endpoint in forbidden_endpoints):
            offenders.append(f"{relative}: native Ollama inference endpoint")
        if any(marker in source for marker in ("OLLAMA_BASE_URL", "OLLAMA_HOST")):
            offenders.append(f"{relative}: Ollama runtime configuration")
        tree = ast.parse(source, filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(module == "ollama" or module.startswith("ollama.") for module in modules):
                offenders.append(f"{relative}: direct Ollama SDK import")
                break
    assert offenders == [], offenders


def test_gateway_is_the_only_module_that_owns_ollama_runtime_configuration():
    source = GATEWAY_PATH.read_text(encoding="utf-8")
    assert 'os.getenv("OLLAMA_BASE_URL"' in source
    assert 'os.getenv("OLLAMA_MODEL"' in source
    assert "get_ollama_config" in source
    assert 'base + "/api/chat"' in source
