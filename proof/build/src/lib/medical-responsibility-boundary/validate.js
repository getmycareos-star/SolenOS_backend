"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.enforceMedicalBoundary = enforceMedicalBoundary;
exports.isMedicalBoundaryGateValid = isMedicalBoundaryGateValid;
const detect_1 = require("./detect");
const rewrite_1 = require("./rewrite");
/**
 * Hard safety gate: detect forbidden clinical authority, rewrite, re-validate.
 * Never returns forbidden content as-is when rewrite succeeds.
 */
function enforceMedicalBoundary(output) {
    const initialViolations = (0, detect_1.detectMedicalBoundaryViolations)(output);
    if (initialViolations.length === 0) {
        return {
            valid: true,
            violations: [],
            rewritten: false,
            output,
        };
    }
    const rewrittenOutput = (0, rewrite_1.rewriteMedicalBoundaryOutput)(output);
    const remainingViolations = (0, detect_1.detectMedicalBoundaryViolations)(rewrittenOutput);
    return {
        valid: remainingViolations.length === 0,
        violations: remainingViolations.length > 0 ? remainingViolations : initialViolations,
        rewritten: true,
        output: rewrittenOutput,
    };
}
function isMedicalBoundaryGateValid(output) {
    return enforceMedicalBoundary(output).valid;
}
