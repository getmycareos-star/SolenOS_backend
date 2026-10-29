from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_graph_has_one_canonical_head_and_unique_revision_ids():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    script = ScriptDirectory.from_config(config)
    revisions = list(script.walk_revisions())
    revision_ids = [revision.revision for revision in revisions]

    assert len(revision_ids) == len(set(revision_ids)), "duplicate Alembic revision IDs found"
    assert len(script.get_heads()) == 1, f"expected one canonical Alembic head, got {script.get_heads()}"

    # These migrations must remain reachable from the canonical head: they add
    # append-only coverage and record baseline/current coverage provenance.
    assert "20261029_10_coverage_immutable" in revision_ids
    assert "20261029_11_cov_prov" in revision_ids
    assert "20261029_12_claim_deps" in revision_ids
    assert "20261029_13_conflict_scope" in revision_ids
    assert "20261029_14_q_answer_hist" in revision_ids
    assert "20261029_16_q_intent" in revision_ids
    assert "20261029_17_pattern_metrics" in revision_ids
    assert "20261029_18_acq_provenance" in revision_ids
    assert "20261029_19_evidence_idem" in revision_ids
    assert "20261029_20_open_loops" in revision_ids
    assert "20261029_21_open_loop_occurrences" in revision_ids
