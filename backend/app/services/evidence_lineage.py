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
    rows = list(evidence_rows)
    # Index every authorized row, not only selected rows. Otherwise a selected
    # derivative and a selected sibling fail to meet at their real parent ID.
    known_ids = {
        str(getattr(row, "id", "") or "")
        for row in rows
        if str(getattr(row, "id", "") or "")
    }
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
    for row in rows:
        evidence_id = str(getattr(row, "id", "") or "")
        if not evidence_id:
            continue
        parent_id = str(getattr(row, "parent_evidence_id", "") or "").strip()
        if parent_id:
            # Join to the real parent node when available. Retain an explicit
            # external node when the parent is outside the authorized query.
            union(evidence_id, parent_id if parent_id in known_ids else "parent:" + parent_id)
            if evidence_id in selected:
                shared_ids.add(evidence_id)

        source_ref = str(getattr(row, "original_source_reference", "") or "").strip()
        if source_ref:
            normalized_ref = " ".join(source_ref.split()).casefold()
            if normalized_ref:
                union(evidence_id, "source-ref:" + normalized_ref)
                if evidence_id in selected:
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



def load_evidence_lineage_scope(
    db: Any,
    person_id: str,
    seed_evidence_ids: Iterable[str],
    *,
    max_depth: int = 12,
    max_nodes: int = 250,
    max_relationships: int = 450,
) -> dict[str, Any]:
    """Load a bounded, same-person provenance closure for workflow evidence.

    Uses reflected columns so legacy/partial schemas remain explicitly usable.
    The visible LCR rows are not the lineage boundary: an older unselected
    ancestor may establish that two visible records share an origin. Only
    explicit parent/source references and persisted relationship edges count.
    """
    from types import SimpleNamespace
    from sqlalchemy import MetaData, Table, func, or_, select

    seeds = {str(value).strip() for value in seed_evidence_ids if str(value).strip()}
    if not seeds:
        return {"evidence_rows": [], "relationships": [], "has_more": False, "truncated": False}

    bind = db.get_bind()
    available_tables = set(__import__("sqlalchemy").inspect(bind).get_table_names())
    if "evidence" not in available_tables:
        return {"evidence_rows": [], "relationships": [], "has_more": False, "truncated": False}
    metadata = MetaData()
    metadata.reflect(bind=bind, only=[
        name for name in ("evidence", "evidence_source_relationships")
        if name in available_tables
    ])
    evidence = metadata.tables["evidence"]
    links = metadata.tables.get("evidence_source_relationships")
    if "id" not in evidence.c or "person_id" not in evidence.c:
        return {"evidence_rows": [], "relationships": [], "has_more": False, "truncated": False}

    evidence_fields = [
        name for name in ("id", "person_id", "parent_evidence_id", "original_source_reference")
        if name in evidence.c
    ]
    evidence_by_id: dict[str, Any] = {}
    relationships_by_id: dict[str, Any] = {}
    frontier = set(seeds)
    has_more = False
    truncated = False
    depth_limit = max(1, min(max_depth, 20))
    node_limit = max(1, min(max_nodes, 1000))
    relationship_limit = max(1, min(max_relationships, 2000))

    for _depth in range(depth_limit):
        if not frontier or len(evidence_by_id) >= node_limit:
            break
        pending = frontier - set(evidence_by_id)
        available = node_limit - len(evidence_by_id)
        statement = select(*(evidence.c[name] for name in evidence_fields)).where(
            evidence.c.person_id == str(person_id),
            evidence.c.id.in_(sorted(pending)),
        ).limit(available + 1) if pending else None
        found = [dict(row) for row in db.execute(statement).mappings().all()] if statement is not None else []
        if len(found) > available:
            has_more = True
            truncated = True
            found = found[:available]
        for row in found:
            evidence_by_id[str(row["id"])] = SimpleNamespace(**row)

        if len(evidence_by_id) >= node_limit:
            has_more = True
            truncated = True
            break

        current_ids = set(evidence_by_id)
        next_ids: set[str] = set()
        source_refs: set[str] = set()
        expansion_rows = [evidence_by_id[evidence_id] for evidence_id in sorted(frontier) if evidence_id in evidence_by_id]
        for row in expansion_rows:
            parent_id = str(getattr(row, "parent_evidence_id", "") or "").strip()
            if parent_id and parent_id not in evidence_by_id:
                next_ids.add(parent_id)
            source_ref = " ".join(str(getattr(row, "original_source_reference", "") or "").split()).casefold()
            if source_ref:
                source_refs.add(source_ref)

        if source_refs and "original_source_reference" in evidence.c:
            available = node_limit - len(evidence_by_id)
            if available <= 0:
                has_more = True
                truncated = True
                break
            ref_statement = select(*(evidence.c[name] for name in evidence_fields)).where(
                evidence.c.person_id == str(person_id),
                evidence.c.original_source_reference.isnot(None),
                func.lower(func.trim(evidence.c.original_source_reference)).in_(sorted(source_refs)),
            ).limit(available + 1)
            ref_rows = [dict(row) for row in db.execute(ref_statement).mappings().all()]
            if len(ref_rows) > available:
                has_more = True
                truncated = True
                ref_rows = ref_rows[:available]
            for row in ref_rows:
                evidence_id = str(row["id"])
                if evidence_id not in evidence_by_id:
                    evidence_by_id[evidence_id] = SimpleNamespace(**row)
                    next_ids.add(evidence_id)

        if links is not None and {"person_id", "source_evidence_id", "related_evidence_id"}.issubset(links.c.keys()):
            relationship_fields = [
                name for name in (
                    "id", "person_id", "source_evidence_id", "related_evidence_id",
                    "source_independence_status", "origin_group_key", "relationship_type",
                    "rationale", "created_at",
                ) if name in links.c
            ]
            remaining = relationship_limit - len(relationships_by_id)
            if remaining <= 0:
                has_more = True
                truncated = True
                break
            link_statement = select(*(links.c[name] for name in relationship_fields)).where(
                links.c.person_id == str(person_id),
                or_(
                    links.c.source_evidence_id.in_(sorted(current_ids)),
                    links.c.related_evidence_id.in_(sorted(current_ids)),
                ),
            ).limit(remaining + 1)
            link_rows = [dict(row) for row in db.execute(link_statement).mappings().all()]
            if len(link_rows) > remaining:
                has_more = True
                truncated = True
                link_rows = link_rows[:remaining]
            for row in link_rows:
                key = str(row.get("id") or (
                    str(row.get("source_evidence_id")) + ":" + str(row.get("related_evidence_id"))
                    + ":" + str(row.get("relationship_type"))
                ))
                relationships_by_id[key] = SimpleNamespace(**row)
                for linked_id in (row.get("source_evidence_id"), row.get("related_evidence_id")):
                    linked_id = str(linked_id or "").strip()
                    if linked_id and linked_id not in evidence_by_id:
                        next_ids.add(linked_id)
        frontier = next_ids

    if frontier - set(evidence_by_id):
        has_more = True
        truncated = True

    # Include edges between discovered nodes while preserving the hard cap.
    discovered = sorted(evidence_by_id)
    if links is not None and discovered and len(relationships_by_id) < relationship_limit:
        relationship_fields = [
            name for name in (
                "id", "person_id", "source_evidence_id", "related_evidence_id",
                "source_independence_status", "origin_group_key", "relationship_type",
                "rationale", "created_at",
            ) if name in links.c
        ]
        remaining = relationship_limit - len(relationships_by_id)
        statement = select(*(links.c[name] for name in relationship_fields)).where(
            links.c.person_id == str(person_id),
            links.c.source_evidence_id.in_(discovered),
            links.c.related_evidence_id.in_(discovered),
        ).limit(remaining + 1)
        extra = [dict(row) for row in db.execute(statement).mappings().all()]
        if len(extra) > remaining:
            has_more = True
            truncated = True
            extra = extra[:remaining]
        for row in extra:
            key = str(row.get("id") or (
                str(row.get("source_evidence_id")) + ":" + str(row.get("related_evidence_id"))
                + ":" + str(row.get("relationship_type"))
            ))
            relationships_by_id[key] = SimpleNamespace(**row)

    return {
        "evidence_rows": list(evidence_by_id.values()),
        "relationships": list(relationships_by_id.values()),
        "has_more": has_more,
        "truncated": truncated,
    }

def summarize_evidence_lineage(
    evidence_rows: Iterable[Any],
    relationships: Iterable[Any],
    selected_evidence_ids: Iterable[str],
) -> dict[str, Any]:
    """Human/audit-readable lineage summary for any LCR-facing workflow."""
    evidence_rows = list(evidence_rows)
    relationships = list(relationships)
    selected = sorted({str(value) for value in selected_evidence_ids})
    lineage_by_id, independence_by_id = build_evidence_lineage(
        evidence_rows, relationships, selected
    )
    grouped: dict[str, list[str]] = {}
    for evidence_id, lineage_id in lineage_by_id.items():
        grouped.setdefault(lineage_id, []).append(evidence_id)

    origin_keys: dict[str, set[str]] = {}
    for relationship in relationships:
        origin_key = str(getattr(relationship, "origin_group_key", "") or "").strip()
        if not origin_key:
            continue
        for evidence_id in (
            str(getattr(relationship, "source_evidence_id", "") or ""),
            str(getattr(relationship, "related_evidence_id", "") or ""),
        ):
            if evidence_id in selected:
                origin_keys.setdefault(lineage_by_id.get(evidence_id, ""), set()).add(origin_key)

    groups = []
    for lineage_id, evidence_ids in sorted(grouped.items()):
        ids = sorted(evidence_ids)
        groups.append({
            "lineage_id": lineage_id,
            "evidence_ids": ids,
            "origin_group_keys": sorted(origin_keys.get(lineage_id, set())),
            "source_independence_status": (
                "shared_origin" if any(independence_by_id.get(eid) == "shared_origin" for eid in ids)
                else "independence_not_assessed"
            ),
            "basis": "explicit_persisted_provenance",
            "interpretation_limit": "A source-lineage relationship does not establish that a claim is true.",
        })
    # Report explicit independent-source reviews separately. They are not
    # merged into shared-origin groups and do not establish factual truth.
    independent_pairs = set()
    for relationship in relationships:
        status = str(getattr(relationship, "source_independence_status", "") or "").strip().lower()
        if status not in INDEPENDENT_STATUSES:
            continue
        left = str(getattr(relationship, "source_evidence_id", "") or "").strip()
        right = str(getattr(relationship, "related_evidence_id", "") or "").strip()
        if left in selected and right in selected and left != right:
            independent_pairs.add(tuple(sorted((left, right))))
    for left, right in sorted(independent_pairs):
        groups.append({
            "lineage_id": "reviewed-independent-pair:" + left + ":" + right,
            "evidence_ids": [left, right],
            "origin_group_keys": [],
            "source_independence_status": "independent",
            "basis": "explicit_independence_review",
            "interpretation_limit": "The recorded independence review does not prove either claim is factually true.",
        })
    groups.sort(key=lambda group: (group["source_independence_status"], group["lineage_id"]))
    return {
        "status": "assessed" if selected else "not_assessed",
        "evidence_ids_considered": selected,
        "groups": groups,
        "interpretation_limit": (
            "Only explicit parent/source references and persisted reviewed relationships are used. "
            "Text similarity does not prove copying, common origin, or independence."
        ),
    }
