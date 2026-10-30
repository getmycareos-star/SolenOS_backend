"""Adversarial source-linking contracts for situation thread formation.

A domain label, subject label, matching timestamp, or generic subject ID is not
sufficient identity evidence. Only overlapping source IDs inside the same
person-scoped reconciliation may join two signals.
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


def test_same_domain_and_subject_label_without_shared_source_stays_separate():
    first = signal("mobility", "walking", ["evidence-a"])
    second = signal("mobility", "walking", ["evidence-b"])

    groups = _group_related_signals([first, second], "evidence_ids")

    assert groups == [[first], [second]]


def test_shared_source_does_not_cross_care_domain_boundary():
    mobility = signal("mobility", "same-label", ["source-1"])
    nutrition = signal("nutrition", "same-label", ["source-1"])

    groups = _group_related_signals([mobility, nutrition], "evidence_ids")

    assert groups == [[mobility], [nutrition]]


def test_explicit_shared_source_joins_signals_even_when_subject_labels_differ():
    first = signal("mobility", "walking", ["source-1"])
    second = signal("mobility", "transfers", ["source-1", "source-2"])

    groups = _group_related_signals([first, second], "evidence_ids")

    assert groups == [[first, second]]


def test_transitive_grouping_requires_a_chain_of_explicit_source_overlap():
    first = signal("sleep", "night-waking", ["source-a"])
    bridge = signal("sleep", "night-waking-copy", ["source-a", "source-b"])
    third = signal("sleep", "night-waking-later", ["source-b"])
    unrelated = signal("sleep", "night-waking", ["source-c"])

    groups = _group_related_signals([first, bridge, third, unrelated], "evidence_ids")

    assert len(groups) == 2
    assert {id(item) for item in groups[0]} == {id(first), id(bridge), id(third)}
    assert groups[1] == [unrelated]


def test_missing_source_ids_never_merge_from_subject_or_time_like_labels():
    first = signal("mobility", "walking", [])
    second = signal("mobility", "walking", [])

    groups = _group_related_signals([first, second], "evidence_ids")

    assert groups == [[first], [second]]
