import json

from app.api.collaboration import (
    _document_extractive_answer,
    _document_reply_contains_source_detail,
)


def _document_line(source_text: str) -> str:
    return "Shared LCR context (source-attributed): " + json.dumps({
        "source_type": "document_extractions",
        "source_id": "document-analysis-1",
        "record": {
            "id": "document-analysis-1",
            "source_text": source_text,
        },
    })


def test_document_question_uses_exact_relevant_extracted_sentence():
    context = [
        _document_line(
            "Mary ate half of her breakfast today. "
            "Mary woke twice overnight. "
            "Mary walked to the kitchen using her walking frame."
        )
    ]

    answer = _document_extractive_answer(
        "What does the uploaded document say about walking?",
        context,
    )

    assert answer == (
        "The uploaded document says: "
        "“Mary walked to the kitchen using her walking frame.”"
    )
    assert not _document_reply_contains_source_detail(
        "It may be helpful to discuss mobility with the care team.",
        answer,
    )
    assert _document_reply_contains_source_detail(
        "Mary walked to the kitchen using her walking frame.",
        answer,
    )


def test_document_question_does_not_invent_an_answer_when_extraction_has_no_match():
    context = [
        _document_line(
            "Mary ate half of her breakfast today. "
            "Mary woke twice overnight."
        )
    ]

    answer = _document_extractive_answer(
        "What does the uploaded document say about walking?",
        context,
    )

    assert answer == (
        "I found the uploaded document, but its extracted text does not clearly "
        "answer that question. I can't confirm it from this document."
    )


def test_broad_document_question_returns_a_short_verified_excerpt():
    context = [
        _document_line(
            "Mary ate half of her breakfast today. "
            "Mary woke twice overnight. "
            "Mary walked to the kitchen using her walking frame. "
            "Mary rested after lunch."
        )
    ]

    answer = _document_extractive_answer("What does the uploaded document say?", context)

    assert answer == (
        "The uploaded document says: “Mary ate half of her breakfast today. ” "
        "Mary woke twice overnight. ” Mary walked to the kitchen using her walking frame.”"
    )


def test_document_question_fails_closed_when_no_verified_extraction_was_supplied():
    answer = _document_extractive_answer(
        "What does the uploaded document say about walking?",
        [],
    )

    assert answer == (
        "I couldn't verify the uploaded document's extracted text, so I can't answer from it."
    )



def test_broad_document_question_returns_only_extracted_sentences():
    context = [
        _document_line(
            "Mary ate half of her breakfast today. "
            "Mary woke twice overnight."
        )
    ]

    answer = _document_extractive_answer(
        "What does the uploaded document say?",
        context,
    )

    assert answer == (
        "The uploaded document says: “Mary ate half of her breakfast today. ” "
        "Mary woke twice overnight.”"
    )
