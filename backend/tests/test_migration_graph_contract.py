"""Guard the Alembic graph against missing historical revisions and split heads."""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_graph_has_one_resolvable_head():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert len(heads) == 1, f"Expected one Alembic head, found: {heads}"

    for revision in script.walk_revisions():
        parents = revision.down_revision
        if parents is None:
            continue
        if isinstance(parents, str):
            parents = (parents,)
        for parent in parents:
            assert script.get_revision(parent) is not None, (
                f"Migration {revision.revision} references missing parent {parent}"
            )

    companion_merge = script.get_revision("20261030_01_companion_turn_merge")
    assert set(companion_merge.down_revision) == {
        "20261029_29_comp_turn_id",
        "20261029_29_ev_rel_scope",
    }

    workflow_merge = script.get_revision("20261102_02_workflow_integrity_merge")
    assert workflow_merge is not None
    assert len(workflow_merge.revision) > 32
    merge_file = backend_dir / "alembic" / "versions" / "20261102_02_workflow_integrity_merge.py"
    merge_source = merge_file.read_text(encoding="utf-8")
    assert 'inspect(bind).has_table("alembic_version")' in merge_source
    assert "String(length=64)" in merge_source
