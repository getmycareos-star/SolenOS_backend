from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_ROOT = BACKEND_ROOT / "app"


def test_manual_demo_seed_script_is_not_shipped():
    """Production source must not offer a script that seeds invented care records."""
    assert not (BACKEND_ROOT / "seed.py").exists()


def test_application_source_contains_no_demo_care_recipient_identity():
    """Demo IDs and named seed personas must never become runtime fallbacks."""
    forbidden_literals = (
        "demo-person-id",
        "demo-caregiver",
        "Alex Johnson",
        "Sam Johnson",
        "sam@example.com",
    )
    for path in APPLICATION_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for literal in forbidden_literals:
            assert literal not in source, f"{literal!r} found in production module {path}"
