import json
import os
import signal
import subprocess
import time
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _wait_for(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.1)
    return path.exists()


def test_cold_ollama_startup_does_not_block_api_or_give_up_after_ten_seconds(tmp_path):
    root = Path(__file__).resolve().parents[2]
    startup_script = root / "start-with-qwen.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    ready_file = tmp_path / "ollama-ready"
    model_file = tmp_path / "model-installed"
    api_started_file = tmp_path / "api-started"
    state_file = tmp_path / "qwen-state.json"

    _write_executable(
        bin_dir / "ollama",
        """#!/usr/bin/env python3
import os
import signal
import sys
import time
from pathlib import Path

ready = Path(os.environ["OLLAMA_READY_FILE"])
model = Path(os.environ["OLLAMA_MODEL_FILE"])
command = sys.argv[1]
if command == "serve":
    time.sleep(12)
    ready.touch()
    signal.pause()
elif command == "show":
    raise SystemExit(0 if model.exists() else 1)
elif command == "pull":
    model.touch()
else:
    raise SystemExit(2)
""",
    )
    _write_executable(
        bin_dir / "curl",
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path
raise SystemExit(0 if Path(os.environ["OLLAMA_READY_FILE"]).exists() else 7)
""",
    )
    (tmp_path / "uvicorn.py").write_text(
        """import os
import time
from pathlib import Path
Path(os.environ["UVICORN_STARTED_FILE"]).touch()
time.sleep(30)
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
            "PYTHONPATH": str(tmp_path) + os.pathsep + env.get("PYTHONPATH", ""),
            "OLLAMA_READY_FILE": str(ready_file),
            "OLLAMA_MODEL_FILE": str(model_file),
            "UVICORN_STARTED_FILE": str(api_started_file),
            "QWEN_INSTALL_STATE_FILE": str(state_file),
            "OLLAMA_MODELS": str(tmp_path / "models"),
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
            "OLLAMA_SIDECAR_ENABLED": "true",
            "OLLAMA_READINESS_TIMEOUT_SECONDS": "30",
            "OLLAMA_PULL_TIMEOUT_SECONDS": "30",
            "OLLAMA_MODEL": "qwen3.5:4b",
        }
    )
    process = subprocess.Popen(
        ["bash", str(startup_script)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = ""
    try:
        assert _wait_for(api_started_file, 5), "FastAPI did not start independently of Ollama warm-up"
        assert not ready_file.exists(), "the test must exercise a cold start longer than the old 10-second probe"
        assert _wait_for(state_file, 25), "Qwen startup state was not written"
        deadline = time.monotonic() + 25
        state = {}
        while time.monotonic() < deadline:
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.1)
                continue
            if state.get("state") == "installed":
                break
            time.sleep(0.1)
        assert state.get("state") == "installed", state
        assert model_file.exists(), "the manager must pull a missing model after Ollama becomes ready"
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        try:
            output = process.communicate(timeout=8)[0] or ""
        except subprocess.TimeoutExpired:
            process.kill()
            output = process.communicate(timeout=5)[0] or ""
    assert "QWEN_INSTALL_STATE state=downloading" in output
    assert "QWEN_INSTALL_STATE state=installed" in output
