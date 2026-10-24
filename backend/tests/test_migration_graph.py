from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_task2_and_care_continuity_migrations_form_one_head():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    script = ScriptDirectory.from_config(config)

    # The legacy caregiver and care-continuity histories must converge on
    # one forward-only merge head after the PostgreSQL privilege migration.
    assert script.get_heads() == ["20261024_09"]
    revisions = list(script.walk_revisions())
    revision_ids = [revision.revision for revision in revisions]
    assert len(revision_ids) == len(set(revision_ids))
