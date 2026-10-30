"""Source-grounded Qwen extraction fallback tests."""
import json

from app.services import qwen_gateway


def test_empty_qwen_extraction_uses_exact_care_sentence_without_paraphrasing(monkeypatch):
    source = "The discharge note states: Patient walked 20 metres with a frame on 12 October."
    monkeypatch.setattr(
        qwen_gateway,
        "qwen_chat",
        lambda *args, **kwargs: json.dumps({"observations": [], "open_questions": []}),
    )

    result = qwen_gateway.extract_review_candidates(
        source,
        source_event_id="",
        source_evidence_id="evidence-123",
    )

    assert result["observations"]
    candidate = result["observations"][0]
    assert candidate["source_quote"] == "Patient walked 20 metres with a frame on 12 October."
    assert candidate["observation"] == candidate["source_quote"]
    assert candidate["source_evidence_id"] == "evidence-123"
    assert candidate["review_status"] == "pending_review"
    assert candidate["extraction_method"] == "deterministic_verbatim_source_sentence"


def test_qwen_candidates_are_augmented_only_with_unrepresented_exact_source_sentences(monkeypatch):
    source = (
        "The patient ate half of lunch. "
        "The patient walked 20 metres with a frame."
    )
    monkeypatch.setattr(
        qwen_gateway,
        "qwen_chat",
        lambda *args, **kwargs: json.dumps({
            "observations": [{
                "subject": "Patient",
                "observation": "Ate half of lunch.",
                "source_quote": "The patient ate half of lunch.",
            }],
            "open_questions": [],
        }),
    )

    result = qwen_gateway.extract_review_candidates(
        source,
        source_event_id="",
        source_evidence_id="evidence-456",
    )

    quotes = [item["source_quote"] for item in result["observations"]]
    assert quotes.count("The patient ate half of lunch.") == 1
    assert "The patient walked 20 metres with a frame." in quotes
    assert all(quote in source for quote in quotes)
