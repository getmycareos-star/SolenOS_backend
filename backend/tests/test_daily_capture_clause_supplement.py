from app.services.capture_review import supplement_source_grounded_daily_observations


def test_daily_capture_supplements_a_second_independent_and_clause_verbatim():
    source = "Mum slept poorly last night and seemed more unsettled this morning."
    result = supplement_source_grounded_daily_observations(source, {
        "observations": [{
            "subject": "Mum",
            "observation": "slept poorly last night",
            "source_quote": "Mum slept poorly last night",
        }],
        "open_questions": [],
    })

    observations = result["observations"]
    assert any(item["observation"] == "slept poorly last night" for item in observations)
    unsettled = next(item for item in observations if "unsettled" in item["observation"])
    assert unsettled["subject"] == "Mum"
    assert unsettled["observation"] == "seemed more unsettled this morning."
    assert unsettled["source_quote"] == source
    assert unsettled["extraction_origin"] == "deterministic_exact_clause_supplement"


def test_daily_capture_supplements_later_clause_without_losing_explicit_subject():
    source = "She ate a little more at dinner and is now resting."
    result = supplement_source_grounded_daily_observations(source, {
        "observations": [{
            "subject": "She",
            "observation": "ate a little more at dinner",
            "source_quote": "She ate a little more at dinner",
        }],
        "open_questions": [],
    })

    resting = next(item for item in result["observations"] if "resting" in item["observation"])
    assert resting["subject"] == "She"
    assert resting["observation"] == "is now resting."
    assert resting["source_quote"] == source


def test_daily_capture_does_not_split_a_conjoined_noun_phrase():
    source = "Mum had tea and toast."
    result = supplement_source_grounded_daily_observations(source, {
        "observations": [{
            "subject": "Mum",
            "observation": "had tea and toast",
            "source_quote": source,
        }],
        "open_questions": [],
    })

    assert len(result["observations"]) == 1
    assert result["observations"][0]["observation"] == "had tea and toast"


def test_daily_capture_supplement_is_idempotent():
    source = "Mum slept poorly last night and seemed more unsettled this morning."
    first = supplement_source_grounded_daily_observations(source, {"observations": [], "open_questions": []})
    second = supplement_source_grounded_daily_observations(source, first)

    assert second["observations"] == first["observations"]
