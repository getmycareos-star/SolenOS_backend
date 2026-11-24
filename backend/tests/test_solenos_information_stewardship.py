from app.services.solenos_identity import SOLENOS_RUNTIME_IDENTITY_CONSTITUTION
from app.services.solenos_personality import SOLENOS_RUNTIME_PERSONALITY_CONSTITUTION
from app.services.qwen_system_prompt import apply_solenos_system_contract
from app.services.solenos_information_stewardship import (
    SOLENOS_INFORMATION_STEWARDSHIP_CONSTITUTION,
    SOLENOS_INFORMATION_STEWARDSHIP_VERSION,
    SOLENOS_RUNTIME_INFORMATION_STEWARDSHIP,
)


def test_full_information_stewardship_constitution_covers_the_complete_lifecycle():
    assert SOLENOS_INFORMATION_STEWARDSHIP_VERSION == "1.0"
    for required in (
        "ACCESS",
        "AVAILABLE KNOWLEDGE",
        "MEMORY / RETENTION",
        "AUTHORITY",
        "COLLECTION",
        "SOURCE PRESERVATION",
        "EXTRACTION",
        "INTERPRETATION",
        "VALIDATION AND RECONCILIATION",
        "REVIEW",
        "PERSISTENCE",
        "RETRIEVAL",
        "CORRECTION / WITHDRAWAL",
        "RETENTION / EXPORT / DELETION",
        "SOURCE AND CLAIM ACCOUNTABILITY",
        "ACTION ACCOUNTABILITY",
        "PRIVACY, SECURITY, AND DATA CONTROL",
        "TRANSPARENCY QUESTION HANDLING",
        "NON-NEGOTIABLE BOUNDARY",
    ):
        assert required in SOLENOS_INFORMATION_STEWARDSHIP_CONSTITUTION


def test_runtime_information_stewardship_is_compact_and_preserves_non_negotiable_rules():
    assert len(SOLENOS_RUNTIME_INFORMATION_STEWARDSHIP) <= 1600
    for required in (
        "Access is what the authenticated user",
        "Available knowledge is only what this request actually receives",
        "Memory/retention is what an authorized service persists",
        "Authority is the backend permission to act",
        "Never infer one from another",
        "Use only authorized, scope-limited context",
        "Repetition is not corroboration",
        "missing or partial retrieval is not proof of absence",
        "Model output is a proposal, never an accepted LCR assertion",
        "required human review",
        "Never claim an action without backend confirmation",
        "Preserve correction lineage and conflicts",
        "Answer access, privacy, security, training, retention, sharing, export, and deletion only from current trusted policy or service results",
        "SolenOS policy prohibits selling sensitive care information or using it for advertising",
        "I can't verify that from the information available to me",
    ):
        assert required in SOLENOS_RUNTIME_INFORMATION_STEWARDSHIP


def test_every_qwen_system_contract_includes_stewardship_after_task_instructions():
    task_instruction = "Workflow-specific instruction: summarize only the supplied care evidence."
    messages = [
        {"role": "system", "content": task_instruction},
        {"role": "user", "content": "What changed?"},
    ]
    assembled = apply_solenos_system_contract(messages)
    system_messages = [item["content"] for item in assembled if item.get("role") == "system"]
    assert len(system_messages) == 1
    system = system_messages[0]
    assert task_instruction in system
    assert SOLENOS_RUNTIME_INFORMATION_STEWARDSHIP in system
    assert SOLENOS_RUNTIME_PERSONALITY_CONSTITUTION in system
    assert SOLENOS_RUNTIME_IDENTITY_CONSTITUTION in system
    assert SOLENOS_INFORMATION_STEWARDSHIP_CONSTITUTION not in system
    assert system.index(task_instruction) < system.index(SOLENOS_RUNTIME_INFORMATION_STEWARDSHIP)
    assert system.index(SOLENOS_RUNTIME_INFORMATION_STEWARDSHIP) < system.index(
        SOLENOS_RUNTIME_PERSONALITY_CONSTITUTION
    )
    assert system.index(SOLENOS_RUNTIME_PERSONALITY_CONSTITUTION) < system.index(
        SOLENOS_RUNTIME_IDENTITY_CONSTITUTION
    )
