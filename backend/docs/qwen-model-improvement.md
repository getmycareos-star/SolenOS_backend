# SolenOS Qwen model improvement

## What this pipeline does

\`backend/scripts/qwen_finetune.py\` performs actual Qwen2.5-0.5B-Instruct LoRA fine-tuning on a manually curated dataset and compares candidate versus base-model held-out token loss. It saves a LoRA adapter only when evaluation loss improves by the configured threshold. It never modifies the base model in place and never deploys a candidate.

This is one quality gate, not proof of safer or better care support. A candidate must also pass the SolenOS care-domain regression suite and human review before any packaging or production rollout.

## Data contract

The script consumes two separate JSONL files: training and evaluation. Every row must include:

- \`example_id\`: stable, non-identifying ID
- \`review_status\`: exactly \`approved\`
- \`consent_active\`: exactly \`true\`, checked against the current consent ledger immediately before export
- \`privacy_review_passed\`: exactly \`true\`, set only after an authorized reviewer has inspected the example
- \`consent_version\`: the version of the consent accepted by the contributor
- \`messages\`: chat messages with user and assistant roles, ending in an assistant response

Do not put names, contact details, raw care-record IDs, access tokens, or source record links in the JSONL. Do not export \`pending_review\` examples. The current application table only stages examples for review; it does not yet provide the full reviewer/export interface. Therefore the training dataset must be prepared through an authorized, audited review process and must not be fabricated from private care notes.

Use distinct contributors/episodes across training and evaluation where feasible, not just distinct row IDs, to reduce leakage. Consent revocation must be checked immediately before dataset creation and again before training starts. If any contributor withdraws, rebuild the dataset and discard affected artifacts.

## Run on a dedicated CUDA GPU

From the backend directory, in an isolated environment with adequate disk and GPU memory:

\`\`\`powershell
python -m pip install -r requirements-training.txt
python scripts/qwen_finetune.py --train C:\\secure\\training.jsonl --eval C:\\secure\\evaluation.jsonl --output C:\\secure\\qwen-candidate
\`\`\`

The script refuses CPU training, unapproved examples, missing active consent, failed privacy review, overlapping train/evaluation IDs, too-small datasets, invalid output directories, and candidates that fail the loss gate.

The training dependencies are intentionally excluded from the Railway production image. Do not run this on the API service or bundle the training stack into the inference image.

## Required before production use

1. Build a privileged review interface/API with immutable audit events, reviewer identity, status transitions, and withdrawal-aware export.
2. Run the dataset exporter against current consent and withdrawal state; produce a non-identifying manifest and contributor/episode-level split.
3. Run training on a controlled GPU worker. The current repository/Railway service is not a training worker.
4. Evaluate held-out task quality, source-grounding, uncertainty, unsafe medical advice, privacy leakage, regression, latency, memory, and rollback behavior.
5. Package the adapter into a model format supported by the chosen Ollama runtime; validate it using real inference against the complete regression suite.
6. Stage the candidate separately, then deliberately approve and deploy it. Production must keep the prior model available for immediate rollback.

## Honest status

The training code and data-validation gates are versioned, but no user data has been trained on by this script, no adapter has been produced, and the production Qwen model has not changed. The application still needs the audited approval/export path, a GPU execution environment, task-level evaluation, Ollama packaging, and a controlled release path.
