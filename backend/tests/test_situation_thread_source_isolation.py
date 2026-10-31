"""Adversarial source-linking contracts for situation thread formation.

Signals join only when both their care domain and subject identity match and
they share an explicit source record. Similar labels, recency, or source reuse
across distinct subjects/domains are not sufficient.
"""
from types import SimpleNamespace

from app.services.situation_formation import _group_related_signals


def signal(domain, subject_id, source_ids):
    return SimpleNamespace(
        subject_type=domain,
        subject_id=subject_id,
        evidence_ids=source_ids,
        event_ids=source_ids,
    )


def test_same_subject_without_shared_source_stays_separate():
    first = signal("mobility", "walking", ["evidence-a"])
    second = signal("mobility", "walking", ["evidence-b"])
    assert _group_related_signals([first, second], "evidence_ids") == [[first], [second]]


def test_shared_source_does_not_cross_care_domain_boundary():
    mobility = signal("mobility", "same-subject", ["source-1"])
    nutrition = signal("nutrition", "same-subject", ["source-1"])
    assert _group_related_signals([mobility, nutrition], "evidence_ids") == [[mobility], [nutrition]]


def test_shared_source_does_not_cross_subject_identity_boundary():
    first = signal("mobility", "walking", ["source-1"])
    second = signal("mobility", "transfers", ["source-1"])
    assert _group_related_signals([first, second], "evidence_ids") == [[first], [second]]


def test_explicit_shared_source_joins_same_domain_and_subject():
    first = signal("mobility", "walking", ["source-1"])
    second = signal("mobility", "walking", ["source-1", "source-2"])
    assert _group_related_signals([first, second], "evidence_ids") == [[first, second]]


def test_transitive_grouping_requires_explicit_source_overlap_and_same_subject():
    first = signal("sleep", "night-waking", ["source-a"])
    bridge = signal("sleep", "night-waking", ["source-a", "source-b"])
    third = signal("sleep", "night-waking", ["source-b"])
    unrelated = signal("sleep", "night-waking", ["source-c"])
    groups = _group_related_signals([first, bridge, third, unrelated], "evidence_ids")
    assert len(groups) == 2
    assert {id(item) for item in groups[0]} == {id(first), id(bridge), id(third)}
    assert groups[1] == [unrelated]


def test_missing_source_ids_never_merge_from_subject_or_time_like_labels():
    first = signal("mobility", "walking", [])
    second = signal("mobility", "walking", [])
    assert _group_related_signals([first, second], "evidence_ids") == [[first], [second]]
