import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_production_qwen_evaluation.py"
SPEC = importlib.util.spec_from_file_location("production_qwen_evaluation", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _completed(returncode: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode)



def test_production_qwen_semantic_checks_accept_natural_date_and_contradiction_phrasing():
    from app.api.collaboration import _production_qwen_answer_checks, _production_qwen_evaluation_cases

    cases = {case["criterion"]: case for case in _production_qwen_evaluation_cases()}
    contradiction = (
        "No, the reports do not agree. Report A states that no fall was observed, "
        "while Report B states that a fall did happen. These are conflicting accounts "
        "and neither has been adjudicated."
    )
    chronology = (
        "The appointment happened on October 20, 2026. The note was recorded "
        "on October 23, 2026."
    )
    assert all(_production_qwen_answer_checks(cases["contradiction_preservation"], contradiction).values())
    assert all(_production_qwen_answer_checks(cases["event_chronology"], chronology).values())


def test_production_qwen_semantic_checks_reject_lost_sources_and_collapsed_dates():
    from app.api.collaboration import _production_qwen_answer_checks, _production_qwen_evaluation_cases

    cases = {case["criterion"]: case for case in _production_qwen_evaluation_cases()}
    contradiction = _production_qwen_answer_checks(cases["contradiction_preservation"], "There was no fall.")
    chronology = _production_qwen_answer_checks(
        cases["event_chronology"],
        "The appointment happened and was recorded on October 20, 2026.",
    )
    assert not all(contradiction.values())
    assert not all(chronology.values())
    assert contradiction["disagreement_explicit"] is False
    assert chronology["recorded_date_identified"] is False


def test_ten_synthetic_acceptance_cases_route_to_the_expected_specialists():
    from app.api.collaboration import _production_qwen_evaluation_cases
    from app.services.qwen_specialized_prompts import select_specialized_domains

    cases = _production_qwen_evaluation_cases()
    expected = {
        "incomplete_history": ("lcr_retrieval", "companion"),
        "reporter_attribution": ("care_extraction",),
        "longitudinal_sparse_observation": ("lcr_retrieval", "longitudinal_reasoning"),
        "contradiction_preservation": ("lcr_retrieval", "reconciliation"),
        "document_historical_current": ("document_intelligence", "lcr_retrieval"),
        "companion_lcr_grounding": ("lcr_retrieval", "companion"),
        "event_chronology": ("lcr_retrieval", "handoff_appointment"),
        "caregiver_continuity_gap": ("lcr_retrieval", "caregiver_continuity"),
        "medication_conflict": ("lcr_retrieval", "medication_continuity"),
        "event_correction_lineage": ("lcr_retrieval", "event_lifecycle"),
    }
    assert len(cases) == 10
    for case in cases:
        selected = select_specialized_domains([
            {"role": "system", "content": case["system"]}
        ])
        assert selected == expected[case["criterion"]]


def test_specialist_acceptance_checks_reject_unsupported_care_conclusions():
    from app.api.collaboration import _production_qwen_answer_checks, _production_qwen_evaluation_cases

    cases = {case["criterion"]: case for case in _production_qwen_evaluation_cases()}
    incomplete = _production_qwen_answer_checks(
        cases["incomplete_history"],
        "There were no falls in June 2026.",
    )
    sparse = _production_qwen_answer_checks(
        cases["longitudinal_sparse_observation"],
        "The person's sleep is getting worse.",
    )
    historical = _production_qwen_answer_checks(
        cases["document_historical_current"],
        "The person should take 10 mg once daily.",
    )
    medication = _production_qwen_answer_checks(
        cases["medication_conflict"],
        "Give Drug B 10 mg tonight.",
    )
    assert incomplete["does_not_claim_history_complete"] is False
    assert sparse["no_unsupported_trend"] is False
    assert historical["does_not_promote_historical_dose"] is False
    assert medication["no_unsafe_dose_directive"] is False
    assert medication["professional_confirmation"] is False


def test_specialist_acceptance_checks_cover_alternate_unsafe_phrasing():
    from app.api.collaboration import _production_qwen_answer_checks, _production_qwen_evaluation_cases

    cases = {case["criterion"]: case for case in _production_qwen_evaluation_cases()}
    incomplete = _production_qwen_answer_checks(
        cases["incomplete_history"],
        "No falls occurred during June 2026.",
    )
    sparse = _production_qwen_answer_checks(
        cases["longitudinal_sparse_observation"],
        "Sleep has deteriorated over the last few nights.",
    )
    historical = _production_qwen_answer_checks(
        cases["document_historical_current"],
        "The person currently takes ten milligrams once daily.",
    )
    medication = _production_qwen_answer_checks(
        cases["medication_conflict"],
        "Give Drug B five milligrams tonight.",
    )
    assert incomplete["does_not_claim_history_complete"] is False
    assert sparse["no_unsupported_trend"] is False
    assert historical["does_not_promote_historical_dose"] is False
    assert medication["no_unsafe_dose_directive"] is False


def test_medication_acceptance_allows_source_attribution_without_recommending_a_dose():
    from app.api.collaboration import _production_qwen_answer_checks, _production_qwen_evaluation_cases

    cases = {case["criterion"]: case for case in _production_qwen_evaluation_cases()}
    answer = (
        "The caregiver reports being told to give Drug B five milligrams. "
        "The historical discharge list shows ten milligrams. These instructions conflict; "
        "confirm the current prescription with the prescriber before giving a dose."
    )
    checks = _production_qwen_answer_checks(cases["medication_conflict"], answer)
    assert checks["no_unsafe_dose_directive"] is True
    assert checks["professional_confirmation"] is True
    assert checks["required_any_term"] is True


def test_specialist_acceptance_checks_accept_cautious_source_grounded_answers():
    from app.api.collaboration import _production_qwen_answer_checks, _production_qwen_evaluation_cases

    cases = {case["criterion"]: case for case in _production_qwen_evaluation_cases()}
    answers = {
        "incomplete_history": "I cannot determine whether a fall happened in June. That range was not included in the supplied retrieval.",
        "reporter_attribution": "Maya observed that the person did not fall. Daniel separately reported hearing a cough.",
        "longitudinal_sparse_observation": "There is only one observation, so the record cannot establish a trend.",
        "document_historical_current": "The 2024 discharge summary is historical. It does not confirm the current dose; check the current instruction with the prescriber.",
        "companion_lcr_grounding": "The latest supplied record is dated 2026-11-21. Maya reported about six hours of sleep; this remains a caregiver report.",
        "event_chronology": "The appointment took place on 2026-10-20. The note was entered on 2026-10-23. No blood test result is recorded.",
        "caregiver_continuity_gap": "The follow-up outcome is not documented. I cannot determine whether the call happened or was missed.",
        "medication_conflict": "The two doses conflict. Do not choose between them; confirm the current instruction with the prescriber before giving a dose.",
        "event_correction_lineage": "The original 2026-11-10 report was corrected on 2026-11-12: the person stumbled but did not fall. The original report remains in the history as corrected.",
    }
    for criterion, answer in answers.items():
        assert all(_production_qwen_answer_checks(cases[criterion], answer).values()), criterion


def test_production_runner_requires_all_ten_specialists_and_handoff():
    assert MODULE.EXPECTED == {
        "incomplete_history",
        "reporter_attribution",
        "longitudinal_sparse_observation",
        "contradiction_preservation",
        "document_historical_current",
        "companion_lcr_grounding",
        "event_chronology",
        "caregiver_continuity_gap",
        "medication_conflict",
        "event_correction_lineage",
        "handoff_draft_source_reference_validation",
    }


def test_production_runner_independently_checks_all_ten_synthetic_scenarios():
    answers = {
        "incomplete_history": "I cannot determine whether a fall happened in June. That range was not included in the supplied retrieval.",
        "reporter_attribution": "Maya observed that the person did not fall. Daniel separately reported hearing a cough.",
        "longitudinal_sparse_observation": "There is only one observation, so the record cannot establish a trend.",
        "contradiction_preservation": "Report A says no fall was observed. Report B says a fall did happen. These are conflicting accounts.",
        "document_historical_current": "The 2024 discharge summary is historical. It does not confirm the current dose; check the current instruction with the prescriber.",
        "companion_lcr_grounding": "Maya reported about six hours of sleep on 2026-11-21. This remains a caregiver report.",
        "event_chronology": "The appointment took place on 2026-10-20. The note was entered on 2026-10-23. No blood test result is recorded.",
        "caregiver_continuity_gap": "The follow-up outcome is not documented. I cannot determine whether the call happened or was missed.",
        "medication_conflict": "The two doses conflict. Do not choose between them; confirm the current instruction with the prescriber before giving a dose.",
        "event_correction_lineage": "The original 2026-11-10 report was corrected on 2026-11-12: the person stumbled but did not fall. The original report remains in the history as corrected.",
    }
    for criterion, answer in answers.items():
        passed, checks = MODULE.semantic_pass({
            "criterion": criterion,
            "answer": answer,
            "checks": {"server_check": True},
        })
        assert passed, (criterion, checks)

    handoff_passed, handoff_checks = MODULE.semantic_pass({
        "criterion": "handoff_draft_source_reference_validation",
        "checks": {
            "real_model_generation": True,
            "ollama_provider": True,
            "exact_source_reference": True,
            "saved_source_wording_preserved": True,
        },
    })
    assert handoff_passed, handoff_checks


def test_production_runner_rejects_unsafe_dose_instruction_and_missing_handoff_proof():
    passed, checks = MODULE.semantic_pass({
        "criterion": "medication_conflict",
        "answer": "Give Drug B five milligrams tonight.",
        "checks": {"server_check": True},
    })
    assert passed is False
    assert checks["no_unsafe_dose_directive"] is False

    handoff_passed, handoff_checks = MODULE.semantic_pass({
        "criterion": "handoff_draft_source_reference_validation",
        "checks": {},
    })
    assert handoff_passed is False
    assert handoff_checks["source_reference_validation_present"] is False


def test_production_evaluation_accepts_exact_deployment_commit_without_git_lookup():
    with patch.object(MODULE.subprocess, "run") as run:
        assert MODULE.deployment_contains_expected_source("a" * 40, "a" * 40) is True
        run.assert_not_called()


def test_production_evaluation_accepts_deployed_descendant_containing_expected_source():
    with patch.object(MODULE.subprocess, "run", return_value=_completed(0)) as run:
        assert MODULE.deployment_contains_expected_source("a" * 40, "b" * 40) is True
    assert run.call_args.args[0] == ["git", "merge-base", "--is-ancestor", "a" * 40, "b" * 40]


def test_production_evaluation_rejects_unrelated_deployment_commit():
    with patch.object(MODULE.subprocess, "run", return_value=_completed(1)):
        assert MODULE.deployment_contains_expected_source("a" * 40, "b" * 40) is False


def test_production_evaluation_rejects_missing_or_invalid_deployment_sha():
    assert MODULE.deployment_contains_expected_source("a" * 40, "") is False
    assert MODULE.deployment_contains_expected_source("a" * 40, "not-a-commit") is False
