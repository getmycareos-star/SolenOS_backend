"""Production guard: fabricated care-record seed data must not ship."""
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent


def test_fabricated_care_record_seed_generator_is_absent():
    assert not (BACKEND_ROOT / "seed.py").exists(), (
        "Synthetic care recipients, caregivers, appointments, evidence, and insights "
        "must not be available as a seed script."
    )


def test_deployment_commands_never_run_a_seed_generator():
    for path in (REPOSITORY_ROOT / "railway.json", REPOSITORY_ROOT / "Procfile"):
        if path.exists():
            source = path.read_text(encoding="utf-8").lower()
            assert "seed.py" not in source
            assert "python -m app.seed" not in source
