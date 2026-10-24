"""Run the staging document smoke against a disposable CI PostgreSQL database.

This starts a real Uvicorn process on loopback, then reuses the same authenticated
document/OCR smoke used for staging. The script refuses non-PostgreSQL DATABASE_URLs.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "http://127.0.0.1:8013"


def ready_status():
    try:
        with urllib.request.urlopen(BASE_URL + "/ready", timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


def main():
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgresql://", "postgresql+psycopg2://")):
        raise SystemExit("Refusing document E2E: DATABASE_URL must be PostgreSQL.")

    log_path = Path("/tmp/solenos-document-e2e-uvicorn.log")
    log = log_path.open("wb")
    os.environ["STAGING_API_BASE_URL"] = BASE_URL
    os.environ["SOLENOS_CI_SMOKE"] = "1"
    env = os.environ.copy()
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8013"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(60):
            if server.poll() is not None:
                detail = log_path.read_text(encoding="utf-8", errors="replace")[-10000:]
                raise RuntimeError(f"Uvicorn exited before document smoke.\\n{detail}")
            if ready_status() == 200:
                break
            time.sleep(0.5)
        else:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-10000:]
            raise RuntimeError(f"Uvicorn did not become ready for document smoke.\\n{detail}")

        from staging_document_smoke import main as run_document_smoke
        run_document_smoke()
        print("PASS: real PostgreSQL HTTP document upload/download, OCR, candidate review attribution, duplicate review rejection, and cross-record denial.")
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        log.close()


if __name__ == "__main__":
    main()
