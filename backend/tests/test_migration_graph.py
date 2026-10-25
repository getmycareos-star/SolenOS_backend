from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_graph_has_one_canonical_head_and_unique_revision_ids():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    revisions = list(script.walk_revisions())
    revision_ids = [revision.revision for revision in revisions]

    assert len(revision_ids) == len(set(revision_ids)), "duplicate Alembic revision IDs found"
    assert script.get_heads() == ["20261025_01"]
