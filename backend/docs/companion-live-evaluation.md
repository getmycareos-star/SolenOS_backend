# Companion live Qwen evaluation

This evaluator sends real requests through `/api/v1/conversation/message` using the configured private Ollama/Qwen endpoint. It is deliberately opt-in and uses an in-memory SQLite database populated only with synthetic records. It does not connect to Railway's production database and does not call a hosted model provider.

## Run from PowerShell

From the backend directory, run this in a runner/container that can actually reach the private Ollama endpoint. The PowerShell syntax below is for setting the variables; a local Windows PC cannot ordinarily resolve a Railway `.railway.internal` hostname unless it is running inside the appropriate network.

```powershell
$env:SOLENOS_RUN_LIVE_QWEN_EVAL = "1"
$env:APP_ENV = "ci"
$env:SECRET_KEY = "ci-only-secret-key-long-enough-for-test-runtime"
$env:OLLAMA_BASE_URL = "http://<your-private-ollama-host>:<port>"
$env:OLLAMA_MODEL = "qwen2.5:0.5b"
python scripts/evaluate_companion_live_qwen.py
```

Replace the host and port with the actual configured private endpoint. Do not use a public or hosted inference URL. The script fails closed if the explicit opt-in or required endpoint/model variables are absent. The repository's Qwen-specific GitHub Actions job is the available repeatable path for running against the bundled model without requiring a paid inference provider.

## Cases

- Retrieves an older sleep event despite 45 newer unrelated events.
- Retrieves a relevant older document despite 25 newer unrelated uploads.
- Resolves a follow-up such as “What about that?” and retrieves the same older event.
- Preserves both source IDs for contradictory medication reports.
- Communicates uncertainty when no blood-pressure record exists.
- Rejects a direct unsupported medication-change instruction.

The JSON output reports each case's pass/fail result, HTTP status, and latency. A nonzero exit code means at least one release criterion failed. A successful run is evidence for that specific model, runtime, and code revision only; rerun it after model/runtime or retrieval changes.

## CI and release gate

The repository has a separate Qwen image-build/E2E workflow that downloads and starts the configured small Qwen model, then exercises real HTTP-to-inference paths. This PR is titled with “Qwen” so that workflow is eligible to run on the pull request. Confirm the job itself completes successfully on the latest head; a skipped job, mocked unit test, or Docker image build alone does not count as real-inference verification.

## Important limitation

This script has been added to make real inference evaluation repeatable, but it must still be executed against a reachable real Qwen runtime before the live-inference release gate can be marked passed. Mock-based unit tests are not a substitute.
