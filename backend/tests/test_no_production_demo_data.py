from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_ROOT = BACKEND_ROOT / "app"


def test_manual_demo_seed_script_is_not_shipped():
    """Production source must not offer a script that seeds invented care records."""
    assert not (BACKEND_ROOT / "seed.py").exists()


def test_application_source_contains_no_demo_care_recipient_identity():
    """Demo IDs and named seed personas must never become runtime fallbacks."""
    forbidden_literals = (
        "-".join(("demo", "person", "id")),
        "-".join(("demo", "caregiver")),
        " ".join(("Alex", "Johnson")),
        " ".join(("Sam", "Johnson")),
        "@".join(("sam", "example.com")),
    )
    for path in APPLICATION_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for literal in forbidden_literals:
            assert literal not in source, f"{literal!r} found in production module {path}"


def test_application_source_has_no_shared_caregiver_or_recipient_fallbacks():
    forbidden_identity_literals = (
        "default_caregiver",
        "default_care_recipient",
        "demo-person-id",
    )
    for path in APPLICATION_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for literal in forbidden_identity_literals:
            assert literal not in source, f"{literal!r} found in production module {path}"


def test_time_patterns_do_not_emit_uncalibrated_count_based_confidence():
    time_intelligence = (APPLICATION_ROOT / "services" / "time_intelligence.py").read_text(encoding="utf-8")
    api_v1 = (APPLICATION_ROOT / "api" / "v1.py").read_text(encoding="utf-8")
    assert "min(count / 10.0, 1.0)" not in time_intelligence
    assert 'confidence="0.9"' not in api_v1
