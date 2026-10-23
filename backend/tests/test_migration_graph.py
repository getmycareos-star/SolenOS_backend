from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_task2_and_care_continuity_migrations_form_one_head():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    script = ScriptDirectory.from_config(config)

    # PR #21 alone ends at the care-circle/Delta merge. When Task 2 is present,
    # the Task 2 migrations must extend that merge as the sole head.
    assert script.get_heads() in (["20261023_05"], ["t2_delivery_attempts_20261023"])
    revisions = list(script.walk_revisions())
    revision_ids = [revision.revision for revision in revisions]
    assert len(revision_ids) == len(set(revision_ids))
