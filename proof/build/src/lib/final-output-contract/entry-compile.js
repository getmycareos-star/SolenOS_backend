"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.compileFromSessionReentry = compileFromSessionReentry;
exports.compileFromInitializationMode = compileFromInitializationMode;
exports.compileFromAdoptionWedge = compileFromAdoptionWedge;
exports.shouldUseEntryCompile = shouldUseEntryCompile;
exports.compileWithEntryBehavior = compileWithEntryBehavior;
const schema_1 = require("./schema");
const degrade_1 = require("./degrade");
const adoption_wedge_engine_1 = require("../adoption-wedge-engine");
const contract_constants_1 = require("../adoption-wedge-engine/contract-constants");
const crisis_overlay_1 = require("./crisis-overlay");
/** Session re-entry: compile strictly from State of Care Summary — no chat, no filler. */
function compileFromSessionReentry(response) {
    const soc = response.state_of_care_summary_layer?.summary;
    if (!soc?.sections)
        return null;
    const sections = soc.sections;
    const attentionCount = response.priority_layer?.attention_events.length ?? 0;
    const behaviorEscalation = response.behavior_interpretation_layer?.escalation.risk_elevation;
    let risk_level = "low";
    if (behaviorEscalation === "high" || response.crisis_mode_interaction_layer?.crisis_mode) {
        risk_level = "high";
    }
    else if (behaviorEscalation === "medium" || attentionCount > 0) {
        risk_level = "medium";
    }
    const draft = {
        what_is_happening: sections.what_is_happening_now.join(" · "),
        what_matters_now: soc.what_matters_most,
        what_to_ask_next: sections.what_should_happen_next.join(" "),
        risk_level,
        what_can_wait: sections.what_is_stable.join(" · ") || "Stable domains noted in current care state.",
        follow_up_items: sections.what_needs_attention.slice(0, 5),
        decision_trace: {
            events: sections.what_changed_recently,
            assumptions: [],
            unknowns: sections.what_remains_uncertain,
            evidence_sources: ["care_context_reconciliation"],
        },
        confidence_state: {
            overall_confidence: (response.continuity_decay_layer?.continuity_confidence_pct ?? 50) >= 70
                ? "medium"
                : "low",
            completeness: response.continuity_decay_layer?.continuity_confidence_pct ?? 50,
            reasoning_limits: [
                "State reconciled from CareContext — no new CareEvent created.",
                ...(response.continuity_decay_layer?.decision_trace_reasons.slice(0, 2) ?? []),
            ],
        },
        trust_layer: response.trust_layer_engine_layer?.trust_layer ?? (0, degrade_1.createEmptyTrustLayer)(),
        transparency_panel: (0, degrade_1.createEmptyTransparencyPanel)(),
    };
    if (response.crisis_mode_interaction_layer?.crisis_mode) {
        return (0, schema_1.validateFinalOutput)((0, crisis_overlay_1.applyCrisisOverlay)(draft, response.crisis_mode_interaction_layer));
    }
    return (0, schema_1.validateFinalOutput)(draft);
}
/** Initialization mode: ingestion-first — no signup wizard or intake forms. */
function compileFromInitializationMode() {
    const accepted = contract_constants_1.ACCEPTED_INPUT_TYPES.map((t) => t.replace(/_/g, " ")).join(", ");
    return (0, schema_1.validateFinalOutput)({
        what_is_happening: adoption_wedge_engine_1.INGESTION_READY_MESSAGE,
        what_matters_now: "Send any care-related content — the system organizes it into structured care truth.",
        what_to_ask_next: `Accepted formats: ${accepted}.`,
        risk_level: "low",
        what_can_wait: "No setup required before first value.",
        follow_up_items: [],
        decision_trace: {
            events: [],
            assumptions: ["Zero-onboarding — first action equals first value."],
            unknowns: ["Care record not yet established — awaiting first input."],
            evidence_sources: [],
        },
        confidence_state: (0, degrade_1.createEmptyConfidenceState)(),
        trust_layer: (0, degrade_1.createEmptyTrustLayer)(),
        transparency_panel: (0, degrade_1.createEmptyTransparencyPanel)(),
    });
}
/** Compile final output from adoption wedge sections — first-interaction contract. */
function compileFromAdoptionWedge(wedge) {
    // Prefer real extracted content — never mask a captured observation as "awaiting first input".
    if (wedge.ingestion_ready && wedge.events_extracted === 0) {
        // kept for empty-session initialization only
    }
    const { structured_summary_of_chaos, current_state_snapshot, actionable_output } = wedge.sections;
    return (0, schema_1.validateFinalOutput)({
        what_is_happening: structured_summary_of_chaos.join(" · ") || adoption_wedge_engine_1.INGESTION_READY_MESSAGE,
        what_matters_now: current_state_snapshot[0] ?? "Awaiting care input to establish current state.",
        what_to_ask_next: actionable_output.join(" "),
        risk_level: wedge.alerts_surfaced > 0 ? "medium" : "low",
        what_can_wait: wedge.ingestion_ready
            ? "No setup required before first value."
            : "Stable items noted in current state snapshot.",
        follow_up_items: actionable_output.slice(0, 5),
        decision_trace: {
            events: structured_summary_of_chaos.slice(0, 5),
            assumptions: [],
            unknowns: wedge.ingestion_ready
                ? ["Care record not yet established — awaiting first input."]
                : [],
            evidence_sources: wedge.ingestion_ready ? [] : ["care_event_extraction"],
        },
        confidence_state: (0, degrade_1.createEmptyConfidenceState)(),
        trust_layer: (0, degrade_1.createEmptyTrustLayer)(),
        transparency_panel: (0, degrade_1.createEmptyTransparencyPanel)(),
    });
}
function shouldUseEntryCompile(response) {
    return (response.entry_behavior_layer?.active === true &&
        (response.entry_behavior_layer.mode === "session_reentry" ||
            response.entry_behavior_layer.mode === "initialization"));
}
function compileWithEntryBehavior(response) {
    if (response.entry_behavior_layer?.mode === "initialization") {
        if (response.adoption_wedge_layer?.active) {
            return compileFromAdoptionWedge(response.adoption_wedge_layer);
        }
        return compileFromInitializationMode();
    }
    if (response.entry_behavior_layer?.mode === "session_reentry") {
        return compileFromSessionReentry(response);
    }
    return null;
}
