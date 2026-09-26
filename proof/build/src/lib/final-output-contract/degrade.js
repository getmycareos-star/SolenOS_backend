"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.createEmptyTransparencyPanel = createEmptyTransparencyPanel;
exports.createEmptyDecisionTrace = createEmptyDecisionTrace;
exports.createEmptyTrustLayer = createEmptyTrustLayer;
exports.createEmptyConfidenceState = createEmptyConfidenceState;
exports.buildDegradedOutput = buildDegradedOutput;
exports.mapRiskToCanonical = mapRiskToCanonical;
exports.canonicalizeRiskLevel = canonicalizeRiskLevel;
exports.mapConfidenceToCanonical = mapConfidenceToCanonical;
exports.computeCompleteness = computeCompleteness;
function createEmptyTransparencyPanel() {
    return {
        data_used: { care_events: [], timeline_segments: [], caregiver_inputs: [] },
        data_ignored: { conflicting: [], low_confidence: [], stale_or_decayed: [] },
        reason_for_output: "Awaiting structured care input to produce traceable reasoning.",
        evidence_breakdown: [],
        confidence_scores: { overall_pct: 15, tier: "low" },
        recency: {
            last_update_at: null,
            critical_event_ages: [],
            decay_status: "stale",
        },
        observed: [],
        inferred: [],
    };
}
function createEmptyDecisionTrace() {
    return {
        events: [],
        assumptions: [],
        unknowns: [],
        evidence_sources: [],
    };
}
function createEmptyTrustLayer() {
    return {
        known: [],
        assumed: [],
        unknown: [{ statement: "Insufficient structure to surface explicit gaps", drives_clarification: true }],
        recency: {
            last_updated_at: null,
            freshness_score: 0,
            interpretation: "potentially outdated (>7–14 days)",
        },
        confidence: 0.15,
    };
}
function createEmptyConfidenceState() {
    return {
        overall_confidence: "low",
        completeness: 0,
        reasoning_limits: ["Insufficient structured information to interpret safely."],
    };
}
/** Degrade into uncertainty fields when output cannot be fully compiled. */
function buildDegradedOutput(input) {
    const questions = input.questions && input.questions.length > 0
        ? input.questions.slice(0, 3).join(" ")
        : "What specifically is happening right now, and when did it start?";
    return {
        what_is_happening: input.partial_happening ??
            "The input does not yet have enough structure to interpret safely. Key details are still uncertain.",
        what_matters_now: "Unable to determine priority — add concrete facts before acting.",
        what_to_ask_next: questions,
        risk_level: "medium",
        what_can_wait: "Priority assessment until missing details are clarified. Absence of detail is not a signal of safety.",
        follow_up_items: [],
        decision_trace: {
            events: [],
            assumptions: [],
            unknowns: input.unknowns ?? [input.reason],
            evidence_sources: ["user input"],
        },
        confidence_state: {
            overall_confidence: "low",
            completeness: 0,
            reasoning_limits: [
                input.reason,
                "Cannot infer events, dates, or relationships without evidence.",
            ],
        },
        trust_layer: {
            known: [],
            assumed: [],
            unknown: (input.unknowns ?? [input.reason]).map((u) => ({
                statement: u,
                drives_clarification: true,
            })),
            recency: {
                last_updated_at: null,
                freshness_score: 0,
                interpretation: "potentially outdated (>7–14 days)",
            },
            confidence: 0.2,
        },
        transparency_panel: createEmptyTransparencyPanel(),
    };
}
function mapRiskToCanonical(risk, attentionCount, hasFailures) {
    if (risk === "high" || risk === "critical" || attentionCount >= 2)
        return "high";
    if (risk === "medium" || hasFailures || attentionCount >= 1)
        return "medium";
    return "low";
}
/** Normalize any risk input to canonical low | medium | high (critical → high). */
function canonicalizeRiskLevel(risk) {
    if (risk === "critical" || risk === "high")
        return "high";
    if (risk === "medium")
        return "medium";
    if (risk === "low")
        return "low";
    return "medium";
}
function mapConfidenceToCanonical(level) {
    if (level === "high")
        return "high";
    if (level === "medium" || level === "insufficient")
        return "medium";
    return "low";
}
function computeCompleteness(understood, uncertain) {
    const total = understood + uncertain;
    if (total === 0)
        return 0;
    return Math.round((understood / total) * 100);
}
