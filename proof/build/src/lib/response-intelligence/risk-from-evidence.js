"use strict";
/**
 * Evidence-only attention risk — held caregiver text, never event kind alone.
 * SoT: docs/17-canonical-architecture/spine-build-sequence.md (Slice 1.2)
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.RISK_FROM_EVIDENCE_PURPOSE = void 0;
exports.inferRiskFromHeldCareEvidence = inferRiskFromHeldCareEvidence;
const care_epistemics_1 = require("../care-epistemics");
const mvp_input_architecture_1 = require("../mvp-input-architecture");
exports.RISK_FROM_EVIDENCE_PURPOSE = "Response Contract risk_level from held care evidence — never from classifyCareEventKind alone.";
function hasOngoingHarmConcern(text) {
    return /\b(still|ongoing|won'?t|can'?t|cannot|unable|bleeding|severe pain|not responding|won'?t wake)\b/i.test(text);
}
function lineWarrantsHarmAttention(text) {
    const t = text.trim();
    if (!t)
        return false;
    if ((0, mvp_input_architecture_1.isRetrospectiveCareReport)(t) && !hasOngoingHarmConcern(t))
        return false;
    if ((0, care_epistemics_1.hasPhysicalHarmSeverityMarkers)(t))
        return true;
    if ((0, care_epistemics_1.hasPhysicalHarmImmediacyMarkers)(t) && (0, care_epistemics_1.mentionsPhysicalHarmEvent)(t))
        return true;
    if ((0, care_epistemics_1.hasRecentHarmTiming)(t) && (0, care_epistemics_1.mentionsPhysicalHarmEvent)(t))
        return true;
    return false;
}
/**
 * Infer risk from held observations — structural harm/immediacy/epistemic severity only.
 */
function inferRiskFromHeldCareEvidence(params) {
    const lines = [...params.heldTexts, params.latestRawText]
        .map((t) => t.trim())
        .filter(Boolean);
    if (lines.some((t) => (0, mvp_input_architecture_1.isImmediateDangerLanguage)(t))) {
        return "high";
    }
    for (const text of lines) {
        if (lineWarrantsHarmAttention(text))
            return "medium";
    }
    for (const text of lines) {
        if ((0, care_epistemics_1.classifyCareSignalSeverity)(text) === "elevated_concern") {
            return "medium";
        }
    }
    return "low";
}
