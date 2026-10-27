from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.services.companion_context import query_terms, rank_context_rows


@dataclass
class Row:
    title: str
    description: str
    occurred_at: datetime


def test_query_terms_discards_low_information_words_but_keeps_care_topics():
    assert query_terms("Has Mum's sleep been getting worse?") >= {"sleep", "worse"}
    assert "mum" not in query_terms("Has Mum's sleep been getting worse?")


def test_retrieval_ranks_older_matching_evidence_above_newer_unrelated_rows():
    now = datetime.now(timezone.utc)
    rows = [
        Row(f"Routine note {index}", "No sleep or fall details recorded here.", now - timedelta(hours=index))
        for index in range(25)
    ]
    older_match = Row("Fall at home", "Caregiver reported a fall during the winter.", now - timedelta(days=240))
    rows.append(older_match)

    selected = rank_context_rows(
        "Did Mum fall last winter?",
        rows,
        text=lambda row: f"{row.title} {row.description}",
        timestamp=lambda row: row.occurred_at,
        limit=12,
        fallback_recent=2,
    )

    assert len(selected) == 12
    assert older_match in selected
    assert selected[0] is older_match


def test_retrieval_is_bounded_and_does_not_mutate_source_rows():
    now = datetime.now(timezone.utc)
    rows = [Row(f"Sleep note {index}", "Poor sleep reported.", now - timedelta(days=index)) for index in range(8)]
    original = [(row.title, row.description, row.occurred_at) for row in rows]

    selected = rank_context_rows(
        "sleep",
        rows,
        text=lambda row: f"{row.title} {row.description}",
        timestamp=lambda row: row.occurred_at,
        limit=3,
    )

    assert len(selected) == 3
    assert [(row.title, row.description, row.occurred_at) for row in rows] == original


def test_recent_baseline_uses_nearest_time_including_upcoming_appointments():
    now = datetime.now(timezone.utc)
    nearest = Row("Routine note", "No appointment details.", now + timedelta(days=3))
    relevant = Row("Clinic appointment", "Review date and follow-up.", now + timedelta(days=200))
    far = Row("Other appointment", "Routine check.", now + timedelta(days=300))

    selected = rank_context_rows(
        "clinic appointment date",
        [far, nearest, relevant],
        text=lambda row: f"{row.title} {row.description}",
        timestamp=lambda row: row.occurred_at,
        limit=2,
        fallback_recent=1,
    )

    assert relevant in selected
    assert nearest in selected
    assert far not in selected
