from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_ROOT = BACKEND_ROOT / "app"


def test_demo_seed_script_is_not_shipped():
    """Synthetic care-record generator must not be present in the runtime repository."""
    assert not (BACKEND_ROOT / "seed.py").exists()


def test_production_modules_have_no_shared_or_demo_identity_fallbacks():
    forbidden = (
        "default_caregiver",
        "default_care_recipient",
        "-".join(("demo", "person", "id")),
        " ".join(("Alex", "Johnson")),
        " ".join(("Sam", "Johnson")),
    )
    for path in APPLICATION_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in source, f"{marker!r} found in production module {path}"


def test_production_modules_do_not_import_demo_seed_code():
    for path in APPLICATION_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "from seed import" not in source
        assert "import seed" not in source
