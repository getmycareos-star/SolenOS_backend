from pathlib import Path
import ast


def test_live_qwen_evaluator_is_syntactically_valid_and_isolated():
    backend_root = Path(__file__).resolve().parents[1]
    evaluator = backend_root / "scripts" / "evaluate_companion_live_qwen.py"
    source = evaluator.read_text(encoding="utf-8")
    ast.parse(source, filename=str(evaluator))

    assert 'SOLENOS_RUN_LIVE_QWEN_EVAL' in source
    assert 'create_engine(' in source
    assert 'sqlite:///:memory:' in source
    assert 'OLLAMA_BASE_URL' in source and 'OLLAMA_MODEL' in source
    assert 'hosted_fallback_used' in source
    assert 'DATABASE_URL' not in source
    assert 'with TestClient(app) as client' not in source
