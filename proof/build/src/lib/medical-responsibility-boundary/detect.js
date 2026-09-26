"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.detectMedicalBoundaryViolations = detectMedicalBoundaryViolations;
exports.isMedicalBoundaryValid = isMedicalBoundaryValid;
const solenos_fields_1 = require("../solenos-fields");
const constants_1 = require("./constants");
function collectFieldText(output) {
    return [(0, solenos_fields_1.collectCaregiverText)(output)];
}
function matchCategory(text, patterns, code, found) {
    for (const pattern of patterns) {
        if (pattern.test(text)) {
            found.add(code);
            return;
        }
    }
}
function detectMedicalBoundaryViolations(output) {
    const combined = collectFieldText(output).join("\n");
    const found = new Set();
    matchCategory(combined, constants_1.DIAGNOSIS_PATTERNS, "diagnosis_language", found);
    matchCategory(combined, constants_1.TREATMENT_PATTERNS, "treatment_recommendation", found);
    matchCategory(combined, constants_1.MEDICATION_INSTRUCTION_PATTERNS, "medication_instruction", found);
    matchCategory(combined, constants_1.CLINICAL_AUTHORITY_PATTERNS, "clinical_authority_override", found);
    matchCategory(combined, constants_1.DIAGNOSTIC_CERTAINTY_PATTERNS, "diagnostic_certainty", found);
    return [...found];
}
function isMedicalBoundaryValid(output) {
    return detectMedicalBoundaryViolations(output).length === 0;
}
