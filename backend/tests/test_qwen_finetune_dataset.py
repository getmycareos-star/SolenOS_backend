import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.qwen_finetune import load_approved_jsonl, validate_split


def example(example_id: str, **overrides):
    row = {
        "example_id": example_id,
        "review_status": "approved",
        "consent_active": True,
        "privacy_review_passed": True,
        "consent_version": "care-improvement-v1",
        "messages": [
            {"role": "user", "content": "Help organize the appointment questions."},
            {"role": "assistant", "content": "The appointment date is not provided."},
        ],
    }
    row.update(overrides)
    return row


def write_rows(path: Path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_loader_accepts_only_approved_consented_privacy_reviewed_rows(tmp_path):
    path = tmp_path / "approved.jsonl"
    write_rows(path, [example("a")])
    assert load_approved_jsonl(path)[0]["example_id"] == "a"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"review_status": "pending_review"}, "human-approved"),
        ({"consent_active": False}, "active improvement consent"),
        ({"privacy_review_passed": False}, "privacy review must pass"),
    ],
)
def test_loader_rejects_unapproved_or_unconsented_rows(tmp_path, overrides, message):
    path = tmp_path / "rejected.jsonl"
    write_rows(path, [example("a", **overrides)])
    with pytest.raises(ValueError, match=message):
        load_approved_jsonl(path)


def test_train_and_eval_must_be_disjoint_and_large_enough():
    train = [example(f"train-{i}") for i in range(20)]
    evaluation = [example(f"eval-{i}") for i in range(5)]
    validate_split(train, evaluation)
    with pytest.raises(ValueError, match="leakage"):
        validate_split(train, [example("train-0")] + evaluation[1:])
    with pytest.raises(ValueError, match="at least 20"):
        validate_split(train[:2], evaluation)


def test_duplicate_ids_are_rejected(tmp_path):
    path = tmp_path / "duplicate.jsonl"
    write_rows(path, [example("same"), example("same")])
    with pytest.raises(ValueError, match="duplicate"):
        load_approved_jsonl(path)
