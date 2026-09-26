"use strict";
/**
 * Source Reliability Layer (SRL) — property of CareEvents, not a separate app.
 * Reliability = truth quality of the input. Confidence = system certainty given inputs.
 * These are independent.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.SOURCE_RELIABILITY_BASELINES = exports.SOURCE_RELIABILITY_TYPES = void 0;
exports.classifySourceReliability = classifySourceReliability;
exports.resolveReliabilityConflict = resolveReliabilityConflict;
exports.SOURCE_RELIABILITY_TYPES = [
    "primary_caregiver",
    "secondary_family",
    "professional_caregiver",
    "clinical",
    "sensor_wearable",
    "patient_self_report",
    "system_inference",
    "unverified",
];
/** Baseline reliability scores 0–1 by source type (input quality). */
exports.SOURCE_RELIABILITY_BASELINES = {
    clinical: 0.95,
    professional_caregiver: 0.85,
    primary_caregiver: 0.8,
    sensor_wearable: 0.75,
    secondary_family: 0.55,
    patient_self_report: 0.45,
    system_inference: 0.4,
    unverified: 0.35,
};
function classifySourceReliability(input) {
    if (input.is_inference) {
        return {
            source_type: "system_inference",
            reliability_score: exports.SOURCE_RELIABILITY_BASELINES.system_inference,
            rationale: "System-derived interpretation — not a grounded observation.",
        };
    }
    const text = `${input.raw_input ?? ""} ${input.attribution_source_type ?? ""}`.toLowerCase();
    if (input.source === "document" ||
        /\b(discharge|physician|doctor|lab|hospital|pharmacy|clinical|diagnosis)\b/i.test(text)) {
        return {
            source_type: "clinical",
            reliability_score: exports.SOURCE_RELIABILITY_BASELINES.clinical,
            rationale: "Clinical / document source — anchors medical facts.",
        };
    }
    if (/\b(nurse|home health|aide|professional caregiver|CNA|RN)\b/i.test(text)) {
        return {
            source_type: "professional_caregiver",
            reliability_score: exports.SOURCE_RELIABILITY_BASELINES.professional_caregiver,
            rationale: "Trained caregiver observation — structured behavioral reliability.",
        };
    }
    if (/\b(sensor|wearable|fitbit|monitor|vitals?)\b/i.test(text)) {
        return {
            source_type: "sensor_wearable",
            reliability_score: exports.SOURCE_RELIABILITY_BASELINES.sensor_wearable,
            rationale: "Objective sensor signal — high for measurement, limited interpretation.",
        };
    }
    if (/\b(brother|sister|sibling|cousin|uncle|aunt|said|told me|someone mentioned)\b/i.test(text)) {
        return {
            source_type: "secondary_family",
            reliability_score: exports.SOURCE_RELIABILITY_BASELINES.secondary_family,
            rationale: "Secondary family report — useful for confirmation, partial context.",
        };
    }
    if (/\b(she said|he said|mom said|dad said|i feel|patient reports)\b/i.test(text)) {
        return {
            source_type: "patient_self_report",
            reliability_score: exports.SOURCE_RELIABILITY_BASELINES.patient_self_report,
            rationale: "Care recipient self-report — variable under cognitive impairment.",
        };
    }
    return {
        source_type: "primary_caregiver",
        reliability_score: exports.SOURCE_RELIABILITY_BASELINES.primary_caregiver,
        rationale: "Primary caregiver direct observation — high contextual accuracy.",
    };
}
/**
 * When sources conflict: do not average blindly.
 * Prefer higher reliability; keep lower as contradiction evidence.
 */
function resolveReliabilityConflict(a, b) {
    const preferred = a.reliability_score >= b.reliability_score ? a : b;
    const subordinate = preferred === a ? b : a;
    return {
        preferred,
        subordinate,
        must_record_contradiction: true,
        lower_global_confidence: true,
    };
}
