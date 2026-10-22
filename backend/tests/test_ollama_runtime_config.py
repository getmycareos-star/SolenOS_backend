from app.core.config import Settings


def test_loopback_ollama_urls_are_detected():
    from app.core.config import is_loopback_ollama_url

    assert is_loopback_ollama_url("http://127.0.0.1:11434")
    assert is_loopback_ollama_url("http://localhost:11434")
    assert is_loopback_ollama_url("http://[::1]:11434")
    assert not is_loopback_ollama_url("http://ollama.railway.internal:11434")
    assert not is_loopback_ollama_url("https://ollama.example.internal")


def test_railway_marks_loopback_ollama_unconfigured(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "test-project")
    settings = Settings(
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="qwen2.5:1.5b",
    )

    assert settings.ollama_configured is False


def test_railway_accepts_private_ollama_service_url(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "test-project")
    settings = Settings(
        ollama_base_url="http://ollama.railway.internal:11434",
        ollama_model="qwen2.5:1.5b",
    )

    assert settings.ollama_configured is True


def test_local_development_can_use_loopback_ollama(monkeypatch):
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    settings = Settings(
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="qwen2.5:1.5b",
    )

    assert settings.ollama_configured is True
