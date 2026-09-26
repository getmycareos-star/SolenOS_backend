"use strict";
/** Policy Engine — runtime-enforced system rules, not static legal pages. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.POLICY_COMPONENTS = exports.POLICY_SOFT_CONSENT_AFTER_CAPTURE = exports.POLICY_CAPTURE_ALWAYS_PRINCIPLE = exports.MEDICAL_ADVICE_REQUEST_PATTERNS = exports.FALSE_CERTAINTY_PATTERNS = exports.ATTRIBUTION_LEAKAGE_PATTERNS = exports.MEDICAL_BOUNDARY_SAFE_ALTERNATIVES = exports.MEDICAL_BOUNDARY_PATTERNS = exports.POLICY_RULES = exports.MULTI_CAREGIVER_SHARING_PROMPT = exports.SIGNUP_IMPROVEMENT_COPY = exports.NO_ADVERTISING_CONSENT_STATEMENT = exports.DATA_IMPROVEMENT_CONSENT_STATEMENT = exports.ONE_LINE_USER_AGREEMENT = exports.TERMS_CONTACT = exports.TERMS_EFFECTIVE_DATE = exports.TERMS_OF_SERVICE_VERSION = exports.POLICY_ENGINE_DEFINING_PRINCIPLE = exports.POLICY_ENGINE_IDENTITY = void 0;
exports.POLICY_ENGINE_IDENTITY = "Policies are execution constraints that every engine must obey before producing or modifying care reality.";
exports.POLICY_ENGINE_DEFINING_PRINCIPLE = "Capture always succeeds — PolicyEngine validates every write; consent gates interpretation and sharing, never raw CareEvent persistence.";
exports.TERMS_OF_SERVICE_VERSION = "2026-07-15";
exports.TERMS_EFFECTIVE_DATE = "15th July, 2026";
exports.TERMS_CONTACT = "davidsolenos@gmail.com";
exports.ONE_LINE_USER_AGREEMENT = "I understand SolenOS is an informational continuity system for caregiving and not a medical service, and I will not use it for clinical decision-making.";
exports.DATA_IMPROVEMENT_CONSENT_STATEMENT = "SolenOS may use my de-identified care data to improve system intelligence, continuity modeling, and safety of care insights.";
exports.NO_ADVERTISING_CONSENT_STATEMENT = "I understand my raw caregiver inputs are never sold or used for advertising.";
exports.SIGNUP_IMPROVEMENT_COPY = "Help improve SolenOS. We may use de-identified care data to improve continuity and safety features. Your personal inputs are never sold or shared.";
exports.MULTI_CAREGIVER_SHARING_PROMPT = "Do you consent to sharing this CareContext with additional caregivers?";
exports.POLICY_RULES = [
    "medical_boundary",
    "privacy_partition",
    "no_raw_data_leakage",
    "uncertainty_required",
    "no_system_invention",
    "soft_consent_after_capture",
];
/** Prohibited medical-advice language in outputs */
exports.MEDICAL_BOUNDARY_PATTERNS = [
    /\b(you have|patient has|diagnosed with|diagnosis is)\b/i,
    /\b(prescribe|prescription should|take \d+ mg|increase dose to|decrease dose to)\b/i,
    /\b(treatment plan|recommended treatment|should treat with)\b/i,
    /\b(call 911 immediately|go to the er now|emergency:)\b/i,
    /\b(clinically proven|definitely has|confirmed diagnosis)\b/i,
];
/** Allowed medical-boundary replacements */
exports.MEDICAL_BOUNDARY_SAFE_ALTERNATIVES = {
    diagnosis: "observation reported — consult a licensed clinician",
    treatment: "information only — not treatment guidance",
    emergency: "urgent pattern noted — contact emergency services or a clinician directly",
};
/** Attribution leakage patterns — must not appear in shared output */
exports.ATTRIBUTION_LEAKAGE_PATTERNS = [
    /\bcaregiver\s+\w+\s+said\b/i,
    /\b(who said|who reported|who told)\b/i,
    /\btheir raw (?:note|input|message)\b/i,
    /\boriginal (?:phrasing|message) from\b/i,
    /\bsubmitted by caregiver\b/i,
];
/** False certainty patterns */
exports.FALSE_CERTAINTY_PATTERNS = [
    /\bdefinitely\b/i,
    /\bwithout doubt\b/i,
    /\bcertainly true\b/i,
    /\b100%\s+(?:sure|certain|confirmed)\b/i,
    /\bguaranteed to\b/i,
];
/** Medical advice / clinical-instruction patterns in caregiver input.
 * Flag for output constraints only — NEVER refuse intake.
 * Worry, fear, and med-change questions must still enter the Living Care Record.
 */
exports.MEDICAL_ADVICE_REQUEST_PATTERNS = [
    // Clinical instruction / prescribe requests (constrain answers — still capture).
    /\b(what should (?:i|we) (?:give|prescribe))\b/i,
    /\b(what should (?:i|we) do about (?:the )?(?:medication|meds|dose|drug|symptoms?|pain|blood pressure|diagnosis))\b/i,
    /\b(is this (?:serious|dangerous|fatal))\b/i,
    /\b(should (?:i|we) (?:stop|start|change) (?:the )?med(?:ication|s)?)\b/i,
    /\bwhat (?:medication|drug|dose) (?:should|to give)\b/i,
];
/** Capture always — consent soft-prompts after persist; medical concern never blocks intake. */
exports.POLICY_CAPTURE_ALWAYS_PRINCIPLE = "Always persist raw input as CareEvents; gate interpretation and sharing; soft-prompt consent after capture; never refuse intake for overwhelm or medical concern.";
/** Soft prompt shown after capture when terms are not yet accepted. */
exports.POLICY_SOFT_CONSENT_AFTER_CAPTURE = "What you shared is preserved in the care record. When you're ready, accept the privacy terms so SolenOS can use this record more fully.";
exports.POLICY_COMPONENTS = [
    "ConsentManager",
    "DataUseRules",
    "PrivacyPartitionRules",
    "MedicalBoundaryRules",
    "AIOutputConstraints",
    "AuditComplianceLogger",
];
