from pathlib import Path


def test_demo_seed_script_is_not_shipped_in_backend_runtime():
    backend_root = Path(__file__).resolve().parents[1]
    assert not (backend_root / "seed.py").exists(), (
        "Synthetic caregiver/person records must not ship in the backend runtime tree."
    )


def test_production_backend_does_not_import_seed_modules():
    backend_root = Path(__file__).resolve().parents[1]
    production_files = [
        path for path in (backend_root / "app").rglob("*.py")
        if path.name != "__init__.py"
    ]
    offenders = [
        str(path.relative_to(backend_root))
        for path in production_files
        if "import seed" in path.read_text(encoding="utf-8")
        or "from seed import" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"Production modules must not import demo seed code: {offenders}"
