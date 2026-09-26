"use strict";
/**
 * Phase 11 — Safety Boundary.
 * Risk ≠ medical advice. Never "you need emergency care." Never diagnose.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.SAFETY_BOUNDARY_BANS = exports.SAFETY_BOUNDARY_HIGH_RISK_FRAMING = void 0;
exports.applySafetyBoundaryToOutput = applySafetyBoundaryToOutput;
exports.containsSafetyBoundaryViolation = containsSafetyBoundaryViolation;
const medical_responsibility_boundary_1 = require("../medical-responsibility-boundary");
exports.SAFETY_BOUNDARY_HIGH_RISK_FRAMING = "Important information may need attention.";
exports.SAFETY_BOUNDARY_BANS = [
    "you need emergency care",
    "go to the er now",
    "call 911 immediately",
    "you have been diagnosed",
    "this confirms the diagnosis",
];
function applySafetyBoundaryToOutput(output, riskLevel) {
    const medical_gate = (0, medical_responsibility_boundary_1.enforceMedicalBoundary)(output);
    const askNext = medical_gate.output.what_to_ask_next;
    const askNextText = Array.isArray(askNext)
        ? askNext.join(" ")
        : typeof askNext === "string"
            ? askNext
            : "";
    const blob = [
        medical_gate.output.what_is_happening,
        medical_gate.output.what_matters_now,
        askNextText,
        medical_gate.output.what_can_wait,
    ]
        .filter(Boolean)
        .join("\n")
        .toLowerCase();
    const violated = exports.SAFETY_BOUNDARY_BANS.some((b) => blob.includes(b));
    let safeOutput = medical_gate.output;
    if (violated) {
        safeOutput = {
            ...medical_gate.output,
            what_matters_now: exports.SAFETY_BOUNDARY_HIGH_RISK_FRAMING,
        };
    }
    return {
        safe: !violated && medical_gate.valid,
        output: safeOutput,
        medical_gate,
        high_risk_framing: riskLevel === "high" ? exports.SAFETY_BOUNDARY_HIGH_RISK_FRAMING : null,
    };
}
function containsSafetyBoundaryViolation(text) {
    const lower = text.toLowerCase();
    return exports.SAFETY_BOUNDARY_BANS.some((b) => lower.includes(b));
}
