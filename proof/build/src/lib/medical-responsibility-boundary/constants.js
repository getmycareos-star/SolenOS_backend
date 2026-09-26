"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.SAFE_UNCERTAINTY_PHRASE = exports.SAFE_CONSULTATION_PHRASE = exports.DIAGNOSTIC_CERTAINTY_PATTERNS = exports.CLINICAL_AUTHORITY_PATTERNS = exports.MEDICATION_INSTRUCTION_PATTERNS = exports.TREATMENT_PATTERNS = exports.DIAGNOSIS_PATTERNS = exports.MEDICAL_BOUNDARY_VIOLATIONS = exports.MEDICAL_BOUNDARY_CAREGIVER_FIRST_ALIGNMENT = void 0;
/**
 * Medical responsibility boundary — aligned with caregiver-first positioning.
 * SolenOS provides clarity and compression for caregivers; it must NEVER become
 * medical authority, diagnostic system, treatment planner, or clinical judgment replacement.
 * See caregiver-first-positioning module: CAREGIVER_FIRST_NEVER_BECOME.
 */
exports.MEDICAL_BOUNDARY_CAREGIVER_FIRST_ALIGNMENT = "Medical boundary enforces caregiver-first positioning: clarity over authority, never diagnosis, treatment, or clinical judgment replacement.";
exports.MEDICAL_BOUNDARY_VIOLATIONS = [
    "diagnosis_language",
    "treatment_recommendation",
    "medication_instruction",
    "clinical_authority_override",
    "diagnostic_certainty",
];
/** Declarative diagnosis framing — interpretive only, never clinical conclusions. */
exports.DIAGNOSIS_PATTERNS = [
    /\bthis is (?!happening|unclear|about|what|when|where|why|how|related)[a-z][\w-]*(?:\s+[a-z][\w-]*){0,4}\b/i,
    /\bthis indicates (?:a |an )?[a-z]/i,
    /\bthis confirms (?:a |an )?[a-z]/i,
    /\byou have (?:a |an )?[a-z][\w-]*(?:\s+[a-z][\w-]*){0,3}\b/i,
    /\b(?:he|she|they) have (?:a |an )?(?:pneumonia|cancer|diabetes|heart failure|stroke|sepsis|infection)\b/i,
    /\bdiagnosed with\b/i,
    /\b(?:signs|symptoms) of (?:a |an )?[a-z]/i,
    /\b(?:likely|probably) has (?:a |an )?[a-z]/i,
    /\b(?:pneumonia|heart failure|cancer progression|sepsis|kidney failure)\b/i,
];
exports.TREATMENT_PATTERNS = [
    /\b(increase|decrease|reduce|raise|lower|stop|start|discontinue|begin|change|adjust)\s+(?:the\s+)?(?:dose|dosage|medication|medicine|drug|prescription|treatment)\b/i,
    /\bshould (?:take|start|stop|increase|decrease|change)\b/i,
    /\brecommend(?:ed|ing)? (?:to )?(?:take|start|stop|increase|decrease)\b/i,
    /\bneeds to (?:take|start|stop|increase|decrease)\b/i,
];
exports.MEDICATION_INSTRUCTION_PATTERNS = [
    /\b(?:take|give|administer)\s+\d+\s*(?:mg|ml|mcg|units?)\b/i,
    /\b(?:take|give)\s+(?:every|twice|once|three times|four times)\b/i,
    /\bswitch to (?:a |an |the )?[a-z]/i,
    /\bsubstitute (?:the |a |an )?(?:medication|medicine|drug)\b/i,
    /\bdouble (?:the )?(?:dose|medication)\b/i,
    /\bhalf (?:the )?(?:dose|medication)\b/i,
];
exports.CLINICAL_AUTHORITY_PATTERNS = [
    /\b(?:ignore|disregard|override|contradict)\s+(?:the\s+)?(?:doctor|physician|clinician|hospital|hospice|nurse)\b/i,
    /\bdo not (?:follow|listen to)\s+(?:the\s+)?(?:doctor|physician|clinician|hospital)\b/i,
    /\binstead of (?:what )?(?:the )?(?:doctor|physician|clinician|hospital)\b/i,
    /\boverrule (?:the )?(?:doctor|physician|clinician)\b/i,
];
exports.DIAGNOSTIC_CERTAINTY_PATTERNS = [
    /\b(?:definitely|certainly|clearly|obviously)\s+(?:has|is|shows|indicates|means)\b/i,
    /\bthis is (?:worsening|progressing|advanced|terminal)\b/i,
    /\bno doubt (?:this|it|that)\b/i,
];
exports.SAFE_CONSULTATION_PHRASE = "Consult a healthcare professional before making any clinical decisions.";
exports.SAFE_UNCERTAINTY_PHRASE = "This pattern may need clinical confirmation from a qualified professional.";
