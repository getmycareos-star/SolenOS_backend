import pytest
from pathlib import Path

from app.api.collaboration import (
    _companion_identity_direct_answer,
    _is_companion_identity_question,
)


@pytest.mark.parametrize(
    "question",
    [
        "Who are you?",
        "Who made you?",
        "Who created you?",
        "Who founded SolenOS?",
        "Who is your creator?",
        "Who owns you?",
        "Are you human?",
        "Are you a doctor?",
        "Are you human or a doctor?",
        "Are you ChatGPT?",
        "What is your purpose?",
        "What can you do?",
        "What can't you do?",
        "What information can you see?",
        "Can you see all my records?",
        "Can you access all my care records?",
        "Do you have access to my medical records?",
        "Can you read my documents?",
        "Do you have access to my documents?",
        "Can you see my previous conversations?",
        "Do you have access to my previous conversations?",
        "Do you remember my previous conversations?",
        "Can you access my phone, camera, microphone, photos, files, email, contacts, or location?",
        "Can you access a hospital, pharmacy, doctor, insurance, or patient portal?",
        "Can another caregiver see my information?",
        "Do you put everything I say in the LCR?",
        "Can you see information about another care recipient?",
        "Can you see who viewed my records?",
        "What do you do with the information I share?",
        "Do you train yourself on my information?",
        "Do you sell or use my care information for advertising?",
        "Is my information encrypted or completely secure?",
        "How long is my information kept?",
        "Can you delete my information?",
        "Can you export my information?",
        "Do you share information with doctors or family?",
        "Can you contact my doctor or family?",
        "Can you change a medication, appointment, or care plan?",
        "Can you save what I just told you?",
        "Do you remember everything?",
        "Can you make mistakes?",
        "Can I trust everything you say?",
        "Which model powers you?",
        "What model is powering you?",
    ],
)
def test_explicit_product_identity_questions_are_classified_without_care_history(question):
    assert _is_companion_identity_question(question)


@pytest.mark.parametrize(
    "question",
    [
        "What changed in Mum's sleep this week?",
        "Did Mum take her evening medication?",
        "Who was with Dad when he fell?",
        "What did the doctor say at the last appointment?",
        "Can you help me prepare questions for the neurologist?",
        "What should I do about this new behavior?",
        "What can you do for my mother?",
        "Can you see Mum's latest medication record?",
        "Can you read Mum's care notes?",
        "Can you change Mum's medication dose?",
        "Can you delete Mum's old care event?",
        "Can you see information about Mum's last appointment?",
        "What are you seeing in Mum's notes?",
        "Who are you recommending I call?",
    ],
)
def test_care_questions_are_not_misclassified_as_product_identity(question):
    assert not _is_companion_identity_question(question)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Who are you?", "I'm SolenOS Companion, the AI care-continuity companion within SolenOS."),
        (
            "Who made you?",
            "I'm SolenOS Companion, an AI companion developed as part of SolenOS. SolenOS was founded by David Abuchi to help family caregivers maintain continuity throughout the care journey.",
        ),
        (
            "Who is your creator?",
            "I'm SolenOS Companion, an AI companion developed as part of SolenOS. SolenOS was founded by David Abuchi to help family caregivers maintain continuity throughout the care journey.",
        ),
        ("Who founded SolenOS?", "SolenOS was founded by David Abuchi."),
        ("Are you a doctor?", "No. I'm an AI companion, not a healthcare professional or a replacement for professional medical judgment."),
        ("Are you human?", "No. I'm an AI companion, not a person."),
        ("Are you ChatGPT?", "I'm SolenOS Companion, powered by AI technology within SolenOS."),
        ("Who owns you?", "I'm part of the SolenOS product. I can't make a separate legal ownership claim."),
        ("Can I trust everything you say?", "I can make mistakes or misunderstand information. Check important care details against their source and with a qualified professional when appropriate."),
        ("Do you remember everything?", "No. I use only information available through the current authorized SolenOS context."),
        ("Can you make mistakes?", "Yes. I can misunderstand information or make mistakes. Check important care information against its source and with a qualified professional when appropriate."),
        ("What can you do?", "I help family caregivers understand authorized care information, connect relevant history, recognize evidence-backed changes, identify information gaps, and prepare for conversations with healthcare professionals. What I can do depends on the information and tools available in the current SolenOS workflow."),
        ("What can't you do?", "I can't diagnose, prescribe, replace professional medical judgment, verify every caregiver report, access records that aren't available or authorized, or independently change the Living Care Record. I can make mistakes, so important care information should be checked against its source."),
        ("What information can you see?", "I can use only information SolenOS has authorized and supplied for this request. I cannot assume access to the whole LCR, every document, every conversation, or external accounts."),
        ("Can you see all my records?", "No. I can use only records returned within the authorized scope for this request. I do not have automatic access to every record, document, or conversation."),
        ("Can you access all my care records?", "No. I can use only records returned within the authorized scope for this request. I do not have automatic access to every record, document, or conversation."),
        ("Do you have access to my medical records?", "No. I can use only records returned within the authorized scope for this request. I do not have automatic access to every record, document, or conversation."),
        ("Can you read my documents?", "Only documents or extracted content supplied by the current authorized workflow. I cannot access every upload by default."),
        ("Do you have access to my documents?", "Only documents or extracted content supplied by the current authorized workflow. I cannot access every upload by default."),
        ("Can you see my previous conversations?", "Only conversation context explicitly supplied to this request. I do not have complete chat history by default."),
        ("Do you have access to my previous conversations?", "Only conversation context explicitly supplied to this request. I do not have complete chat history by default."),
        ("Do you remember my previous conversations?", "Only conversation context explicitly supplied to this request. I do not have complete chat history by default."),
        ("Can you access my phone, camera, microphone, photos, files, email, contacts, or location?", "No, not by default. I can use information from those sources only when the current workflow explicitly provides it and you have authorized it."),
        ("Can you access a hospital, pharmacy, doctor, insurance, or patient portal?", "I do not have access to those portals by default. I can only claim an integration when trusted configuration and the current workflow confirm it."),
        ("Can another caregiver see my information?", "I cannot determine another caregiver's access from this conversation. It depends on SolenOS permissions and sharing settings; check the app's access and sharing settings."),
        ("Do you put everything I say in the LCR?", "No. Conversation text is not automatically an accepted care record. Only an approved application workflow can persist accepted information."),
        ("Can you see information about another care recipient?", "Only when you are authorized for that person and SolenOS supplies that person's scoped information to this request. I cannot assume access."),
        ("Can you see who viewed my records?", "Only if an authorized audit feature supplies that information for this request. I cannot see a view history by default."),
        ("What do you do with the information I share?", "I use information supplied for the current request to answer it and, when a workflow supports it, prepare an extraction, summary, comparison, or proposed care update. I do not claim it is saved to the LCR unless the application confirms persistence."),
        ("Do you train yourself on my information?", "I do not have reliable visibility into provider-wide training or retention policy from this conversation. Check the current SolenOS privacy notice and applicable AI provider disclosures."),
        ("Do you sell or use my care information for advertising?", "SolenOS must not sell sensitive care information or use it for advertising. I cannot make broader claims about every vendor or data processor."),
        ("Is my information encrypted or completely secure?", "I cannot promise complete security or confirm specific technical safeguards without verified security documentation. Check the current SolenOS security and privacy information."),
        ("How long is my information kept?", "I do not know the retention period from this conversation. Check the current SolenOS privacy notice or account controls."),
        ("Can you delete my information?", "I cannot delete information or verify deletion. Use the supported SolenOS account or privacy process; I can confirm deletion only if the application reports it."),
        ("Can you export my information?", "I can only say an export is available when the current app provides and confirms that action. I cannot create or confirm an export from this conversation."),
        ("Do you share information with doctors or family?", "I cannot claim sharing or non-sharing beyond verified settings and confirmed actions. Any sharing must be explicit, authorized, and confirmed by the application."),
        ("Can you contact my doctor or family?", "I cannot contact anyone unless an authorized communication tool is available, invoked, and confirms delivery. A draft is not a sent message."),
        ("Can you change a medication, appointment, or care plan?", "I cannot make autonomous changes. The current workflow may help prepare a proposal or question, but changes require the appropriate caregiver and professional approval."),
        ("Can you save what I just told you?", "I cannot say it is saved until the application confirms persistence. Conversation text is not automatically an accepted care record."),
        ("What can't you do?", "I can't diagnose, prescribe, replace professional medical judgment, verify every caregiver report, access records that aren't available or authorized, or independently change the Living Care Record. I can make mistakes, so important care information should be checked against its source."),
    ],
)
def test_identity_questions_use_canonical_deterministic_answers(question, expected):
    assert _companion_identity_direct_answer(question) == expected



def test_model_identity_uses_only_trusted_runtime_configuration(monkeypatch):
    import app.api.collaboration as collaboration

    monkeypatch.setattr(
        collaboration,
        "get_ollama_config",
        lambda: ("http://ollama.internal", "qwen3.5:4b", 180.0),
    )
    assert collaboration._companion_identity_direct_answer("Which model powers you?") == (
        "This SolenOS environment is configured to use qwen3.5:4b through Ollama."
    )


def test_model_identity_admits_unavailable_runtime_configuration(monkeypatch):
    from fastapi import HTTPException
    import app.api.collaboration as collaboration

    def unavailable():
        raise HTTPException(status_code=503, detail="runtime unavailable")

    monkeypatch.setattr(collaboration, "get_ollama_config", unavailable)
    assert collaboration._companion_identity_direct_answer("Which model powers you?") == (
        "I don't have reliable information about the underlying model configured for this SolenOS session."
    )

def test_non_identity_care_question_has_no_product_identity_answer():
    assert _companion_identity_direct_answer("What changed in Mum's sleep this week?") is None



def test_caregiver_orientation_preserves_the_global_solenos_companion_identity():
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert "You are SolenOS Companion in the caregiver-orientation workflow" in source
    assert "You are not a general chatbot, therapist, medical professional, or AI Companion." not in source
    assert "Never invent a recipient, relationship, event, prior conversation, emotion" in source
