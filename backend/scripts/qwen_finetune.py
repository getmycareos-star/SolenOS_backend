"""Train and evaluate a consent-reviewed Qwen LoRA adapter.

This command deliberately reads only an explicitly curated JSONL export. It never
queries the care database, scrapes notes, or auto-exports pending feedback.
Requires a CUDA GPU and the optional requirements-training.txt dependencies.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

MIN_TRAIN_EXAMPLES = 20
MIN_EVAL_EXAMPLES = 5
DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"


def load_approved_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            example_id = row.get("example_id")
            messages = row.get("messages")
            if not isinstance(example_id, str) or not example_id.strip():
                raise ValueError(f"{path}:{line_number}: example_id is required")
            if example_id in seen_ids:
                raise ValueError(f"{path}:{line_number}: duplicate example_id")
            seen_ids.add(example_id)
            if row.get("review_status") != "approved":
                raise ValueError(f"{path}:{line_number}: example is not human-approved")
            if row.get("consent_active") is not True:
                raise ValueError(f"{path}:{line_number}: active improvement consent required")
            if row.get("privacy_review_passed") is not True:
                raise ValueError(f"{path}:{line_number}: privacy review must pass")
            if not isinstance(row.get("consent_version"), str) or not row["consent_version"].strip():
                raise ValueError(f"{path}:{line_number}: consent_version is required")
            if not isinstance(messages, list) or len(messages) < 2:
                raise ValueError(f"{path}:{line_number}: messages must contain user and assistant")
            roles = [m.get("role") for m in messages if isinstance(m, dict)]
            if len(roles) != len(messages) or "user" not in roles or "assistant" not in roles:
                raise ValueError(f"{path}:{line_number}: valid user and assistant messages required")
            if roles[-1] != "assistant":
                raise ValueError(f"{path}:{line_number}: final message must be assistant output")
            for message in messages:
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError(f"{path}:{line_number}: message content cannot be empty")
                if len(content) > 12_000:
                    raise ValueError(f"{path}:{line_number}: message exceeds 12,000 characters")
            rows.append(row)
    if not rows:
        raise ValueError(f"{path}: no approved examples found")
    return rows


def validate_split(train: list[dict[str, Any]], evaluation: list[dict[str, Any]]) -> None:
    train_ids = {row["example_id"] for row in train}
    eval_ids = {row["example_id"] for row in evaluation}
    overlap = train_ids & eval_ids
    if overlap:
        raise ValueError(f"train/evaluation leakage: {len(overlap)} shared example IDs")
    if len(train) < MIN_TRAIN_EXAMPLES:
        raise ValueError(f"at least {MIN_TRAIN_EXAMPLES} approved training examples required")
    if len(evaluation) < MIN_EVAL_EXAMPLES:
        raise ValueError(f"at least {MIN_EVAL_EXAMPLES} separate evaluation examples required")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True, help="Curated approved training JSONL")
    parser.add_argument("--eval", type=Path, required=True, help="Separate curated approved evaluation JSONL")
    parser.add_argument("--output", type=Path, required=True, help="New, empty output directory")
    parser.add_argument("--model", default=os.getenv("SOLENOS_TRAINING_MODEL", DEFAULT_MODEL))
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--min-relative-improvement", type=float, default=0.01)
    args = parser.parse_args()

    try:
        train_rows = load_approved_jsonl(args.train)
        eval_rows = load_approved_jsonl(args.eval)
        validate_split(train_rows, eval_rows)
    except (OSError, ValueError) as exc:
        print(f"DATASET REJECTED: {exc}", file=sys.stderr)
        return 2

    if not 0.0 <= args.min_relative_improvement <= 0.5:
        print("--min-relative-improvement must be between 0 and 0.5", file=sys.stderr)
        return 2
    if args.epochs <= 0 or args.epochs > 5:
        print("--epochs must be greater than 0 and at most 5", file=sys.stderr)
        return 2
    if args.output.exists() and any(args.output.iterdir()):
        print("OUTPUT REJECTED: output directory must be empty", file=sys.stderr)
        return 2

    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            DataCollatorForLanguageModeling,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        print(
            "TRAINING DEPENDENCIES MISSING: install backend/requirements-training.txt",
            file=sys.stderr,
        )
        return 3

    if not torch.cuda.is_available():
        print("TRAINING BLOCKED: CUDA GPU required; refusing slow/unverified CPU training.", file=sys.stderr)
        return 4
    if args.output.resolve() in {args.train.resolve().parent, args.eval.resolve().parent}:
        print("OUTPUT REJECTED: output cannot replace a dataset directory", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=False)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def render(row: dict[str, Any]) -> dict[str, str]:
        text = tokenizer.apply_chat_template(
            row["messages"], tokenize=False, add_generation_prompt=False
        )
        return {"text": text}

    train_ds = Dataset.from_list([render(row) for row in train_rows])
    eval_ds = Dataset.from_list([render(row) for row in eval_rows])

    def tokenize(batch: dict[str, list[str]]) -> dict[str, Any]:
        return tokenizer(batch["text"], truncation=True, max_length=2048)

    train_ds = train_ds.map(tokenize, batched=True, remove_columns=["text"])
    eval_ds = eval_ds.map(tokenize, batched=True, remove_columns=["text"])

    base_model = AutoModelForCausalLM.from_pretrained(
        args.model, torch_dtype=torch.float16, trust_remote_code=False
    )
    base_model.config.use_cache = False
    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
    baseline_args = TrainingArguments(
        output_dir=str(args.output / "baseline-eval"),
        per_device_eval_batch_size=1,
        report_to=[],
        fp16=True,
        dataloader_pin_memory=True,
    )
    baseline_trainer = Trainer(
        model=base_model,
        args=baseline_args,
        eval_dataset=eval_ds,
        data_collator=collator,
    )
    baseline_loss = float(baseline_trainer.evaluate()["eval_loss"])
    del baseline_trainer
    del base_model
    torch.cuda.empty_cache()

    candidate = AutoModelForCausalLM.from_pretrained(
        args.model, torch_dtype=torch.float16, trust_remote_code=False
    )
    candidate.config.use_cache = False
    candidate = get_peft_model(
        candidate,
        LoraConfig(
            r=8,
            lora_alpha=16,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        ),
    )
    candidate_args = TrainingArguments(
        output_dir=str(args.output / "trainer-state"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=2e-4,
        warmup_ratio=0.05,
        weight_decay=0.01,
        logging_steps=5,
        eval_strategy="epoch",
        save_strategy="no",
        report_to=[],
        fp16=True,
        gradient_checkpointing=True,
        seed=42,
        data_seed=42,
    )
    trainer = Trainer(
        model=candidate,
        args=candidate_args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        data_collator=collator,
    )
    trainer.train()
    candidate_loss = float(trainer.evaluate()["eval_loss"])
    required_loss = baseline_loss * (1.0 - args.min_relative_improvement)
    accepted = math.isfinite(candidate_loss) and candidate_loss <= required_loss

    manifest = {
        "base_model": args.model,
        "training_examples": len(train_rows),
        "evaluation_examples": len(eval_rows),
        "baseline_eval_loss": baseline_loss,
        "candidate_eval_loss": candidate_loss,
        "minimum_relative_improvement": args.min_relative_improvement,
        "accepted_by_loss_gate": accepted,
        "status": "candidate_passed_loss_gate" if accepted else "rejected_no_proven_improvement",
        "note": (
            "Loss is a necessary screening metric, not proof of clinical safety or overall quality. "
            "Run the separate care-domain regression suite and human review before any release."
        ),
    }
    (args.output / "evaluation.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    if accepted:
        candidate.save_pretrained(args.output / "adapter")
        tokenizer.save_pretrained(args.output / "adapter")
        print(json.dumps(manifest, indent=2))
        return 0

    print(json.dumps(manifest, indent=2))
    print("CANDIDATE REJECTED: eval loss did not improve by the configured threshold.", file=sys.stderr)
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
