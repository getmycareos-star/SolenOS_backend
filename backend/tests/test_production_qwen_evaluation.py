import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_production_qwen_evaluation.py"
SPEC = importlib.util.spec_from_file_location("production_qwen_evaluation", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _completed(returncode: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode)


def test_production_evaluation_accepts_exact_deployment_commit_without_git_lookup():
    with patch.object(MODULE.subprocess, "run") as run:
        assert MODULE.deployment_contains_expected_source("a" * 40, "a" * 40) is True
        run.assert_not_called()


def test_production_evaluation_accepts_deployed_descendant_containing_expected_source():
    with patch.object(MODULE.subprocess, "run", return_value=_completed(0)) as run:
        assert MODULE.deployment_contains_expected_source("a" * 40, "b" * 40) is True
    assert run.call_args.args[0] == ["git", "merge-base", "--is-ancestor", "a" * 40, "b" * 40]


def test_production_evaluation_rejects_unrelated_deployment_commit():
    with patch.object(MODULE.subprocess, "run", return_value=_completed(1)):
        assert MODULE.deployment_contains_expected_source("a" * 40, "b" * 40) is False


def test_production_evaluation_rejects_missing_or_invalid_deployment_sha():
    assert MODULE.deployment_contains_expected_source("a" * 40, "") is False
    assert MODULE.deployment_contains_expected_source("a" * 40, "not-a-commit") is False
