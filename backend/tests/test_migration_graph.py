from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_task2_and_care_continuity_migrations_form_one_head():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    script = ScriptDirectory.from_config(config)

    # PR #21, Task 2, Task 3 handoffs, and Task 4 document assets must form one linear chain.
    assert script.get_heads() == ["t4_document_assets_20261023"]
    revisions = list(script.walk_revisions())
    revision_ids = [revision.revision for revision in revisions]
    assert len(revision_ids) == len(set(revision_ids))

    down_revisions = {
        revision.revision: revision.down_revision
        for revision in revisions
    }
    assert down_revisions["20261022_01"] == "20261022_0005"
    assert down_revisions["t2_completion_20261023"] == "20261023_05"
    assert down_revisions["t2_caregiver_schema_reconcile_20261024"] == "t2_delivery_attempts_20261023"
    assert down_revisions["t2_auth_reconcile_20261023"] == "t2_caregiver_schema_reconcile_20261024"
    assert down_revisions["t3_care_handoffs_20261023"] == "t2_auth_reconcile_20261023"
    assert down_revisions["t4_document_assets_20261023"] == "t3_care_handoffs_20261023"


def test_alembic_root_logger_configuration_supports_upgrade_smoke_test():
    backend_dir = Path(__file__).resolve().parents[1]
    config_text = (backend_dir / "alembic.ini").read_text(encoding="utf-8")
    root_section = config_text.split("[logger_root]", 1)[1].split("[", 1)[0]
    assert "qualname = root" in root_section
