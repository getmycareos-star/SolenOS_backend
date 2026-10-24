from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_task2_and_care_continuity_migrations_form_one_head():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    script = ScriptDirectory.from_config(config)

    # Task 2 extends the care-continuity graph through delivery-attempt persistence.
    # Task 5 must continue that same graph as one deployable head.
    assert script.get_heads() == ["t5_proactive_items_20261024"]
    revisions = list(script.walk_revisions())
    revision_ids = [revision.revision for revision in revisions]
    assert len(revision_ids) == len(set(revision_ids))


def test_alembic_root_logger_configuration_supports_upgrade_smoke_test():
    backend_dir = Path(__file__).resolve().parents[1]
    config_text = (backend_dir / "alembic.ini").read_text(encoding="utf-8")
    root_section = config_text.split("[logger_root]", 1)[1].split("[", 1)[0]
    assert "qualname = root" in root_section
