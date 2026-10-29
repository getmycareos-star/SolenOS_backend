"""Conservative source-lineage projection for evidence-grounded assessments.

Only explicit provenance links create shared-source groups. Text similarity and
matching source text are deliberately excluded: they are duplicate candidates,
not proof of common origin.
"""
from __future__ import annotations

from typing import Any, Iterable


INDEPENDENT_STATUSES = {"independent", "independent_source", "independently_observed"}


def build_evidence_lineage(
    evidence_rows: Iterable[Any],
    relationships: Iterable[Any],
    selected_evidence_ids: Iterable[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Return stable lineage IDs and independence labels for selected evidence.

    Callers must supply evidence and relationship rows already scoped to one
    authorized person. Parent IDs, original source references, explicit origin
    groups, and reviewed source relationships are provenance links; no content
    similarity is used to assert common origin.
    """
    selected = {str(value) for value in selected_evidence_ids}
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            first, second = sorted((left_root, right_root))
            parent[second] = first

    shared_ids: set[str] = set()
    for row in evidence_rows:
        evidence_id = str(getattr(row, "id", "") or "")
        if evidence_id not in selected:
            continue
        parent_id = str(getattr(row, "parent_evidence_id", "") or "").strip()
        if parent_id:
            union(evidence_id, "parent:" + parent_id)
            shared_ids.add(evidence_id)

        source_ref = str(getattr(row, "original_source_reference", "") or "").strip()
        if source_ref:
            normalized_ref = " ".join(source_ref.split()).casefold()
            if normalized_ref:
                union(evidence_id, "source-ref:" + normalized_ref)
                shared_ids.add(evidence_id)

    independence: dict[str, str] = {}
    for relationship in relationships:
        left = str(getattr(relationship, "source_evidence_id", "") or "").strip()
        right = str(getattr(relationship, "related_evidence_id", "") or "").strip()
        if not left or not right:
            continue
        status = str(getattr(relationship, "source_independence_status", "") or "").strip().lower()
        if status in INDEPENDENT_STATUSES:
            for evidence_id in (left, right):
                if evidence_id in selected:
                    independence.setdefault(evidence_id, "independent")
            continue

        union(left, right)
        for evidence_id in (left, right):
            if evidence_id in selected:
                independence[evidence_id] = "shared_origin"
                shared_ids.add(evidence_id)

        origin_group = str(getattr(relationship, "origin_group_key", "") or "").strip()
        if origin_group:
            node = "origin-group:" + origin_group
            union(left, node)
            union(right, node)

    groups: dict[str, list[str]] = {}
    for evidence_id in selected:
        root = find(evidence_id)
        groups.setdefault(root, []).append(evidence_id)

    lineage = {
        evidence_id: "persisted-lineage:" + min(members)
        for members in groups.values()
        if len(members) > 1
        for evidence_id in members
    }
    # A single selected item with a parent/source reference still carries lineage
    # to a node outside the selected set, and must not be counted as independent.
    for evidence_id in shared_ids:
        if evidence_id in selected:
            independence[evidence_id] = "shared_origin"
            lineage.setdefault(evidence_id, "persisted-lineage:" + evidence_id)

    return lineage, independence
