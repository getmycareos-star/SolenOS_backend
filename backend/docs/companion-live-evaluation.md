# Companion live Qwen evaluation

This evaluator sends real requests through `/api/v1/conversation/message` using the configured private Ollama/Qwen endpoint. It is deliberately opt-in and uses an in-memory SQLite database populated only with synthetic records. It does not connect to Railway's production database and does not call a hosted model provider.

## Run from PowerShell

From the backend directory, configure the exact private Ollama endpoint and model already available to your test environment:

```powershell
$env:SOLENOS_RUN_LIVE_QWEN_EVAL = "1"
$env:OLLAMA_BASE_URL = "http://<your-private-ollama-host>:<port>"
$env:OLLAMA_MODEL = "qwen2.5:0.5b"
python scripts/evaluate_companion_live_qwen.py
```

Replace the host and port with the actual configured private endpoint. Do not use a public or hosted inference URL. The script fails closed if the explicit opt-in or required endpoint/model variables are absent.

## Cases

- Retrieves an older sleep event despite 45 newer unrelated events.
- Retrieves a relevant older document despite 25 newer unrelated uploads.
- Resolves a follow-up such as “What about that?” and retrieves the same older event.
- Preserves both source IDs for contradictory medication reports.
- Communicates uncertainty when no blood-pressure record exists.
- Rejects a direct unsupported medication-change instruction.

The JSON output reports each case's pass/fail result, HTTP status, and latency. A nonzero exit code means at least one release criterion failed. A successful run is evidence for that specific model, runtime, and code revision only; rerun it after model/runtime or retrieval changes.

## Important limitation

This script has been added to make real inference evaluation repeatable, but it must still be executed against a reachable real Qwen runtime before the live-inference release gate can be marked passed. Mock-based unit tests are not a substitute.
