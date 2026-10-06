from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "SolenOS"
    debug: bool = True
    database_url: str = "sqlite:///./solenos.db"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    # Local-only language model (Ollama). No cloud endpoint is configurable on
    # purpose: caregiver data must never leave the machine it was entered on.
    #
    # Default is the smallest model that reliably loads on a modest machine.
    # This is a deliberate choice rather than a shrug: the reasoning layer is
    # assistive, every caller has a deterministic fallback, and the trust guard
    # treats model output as untrusted until checked. A weaker model costs
    # enrichment quality, never safety. Point this at a larger model with
    # llm_model= once one loads in reasonable time.
    llm_enabled: bool = True
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5-coder:1.5b"
    # A measured conversation reply on a modest machine takes roughly 35s, so a
    # 45s budget sat close enough to the edge that any background load turned it
    # into a timeout. Timeouts are not a safe failure mode here: the engine
    # treats an unavailable model as "no local reasoning", the guard then strips
    # every citation, and the caregiver gets "I could not give you an answer" to
    # a question the record can answer. The user is waiting on an answer about
    # their mother, so the budget is set well above the real cost and is
    # configurable for faster hardware.
    llm_timeout_seconds: float = 120.0
    llm_temperature: float = 0.1
    llm_context_tokens: int = 8192
    # Bounded output. A small local model will happily write 2000 tokens of
    # padding, which on a modest machine is most of the wait, and a long answer
    # is a long answer to audit. The conversation asks for two or three
    # sentences, so a small ceiling is enough and keeps a rambling turn from
    # pushing the call past the timeout.
    llm_max_output_tokens: int = 400
    # How long the local runtime keeps the model resident after a call. The
    # runtime's own default unloads it quickly, and reloading costs far more
    # than answering: measured here, a warm reply is ~2s and a cold one ~72s.
    # A caregiver opens the app, asks a few questions and closes it, which is
    # exactly the pattern that guarantees a reload on the next question and a
    # timeout with it. Set to "0" to release memory immediately instead.
    llm_keep_alive: str = "30m"

    # A local model is fast but never allowed to be the only thing standing
    # between a hallucination and a caregiver. When reasoning is unavailable the
    # deterministic engines must still answer, so failures open a circuit
    # instead of retrying on every request.
    llm_circuit_failure_threshold: int = 3
    llm_circuit_reset_seconds: float = 120.0

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
