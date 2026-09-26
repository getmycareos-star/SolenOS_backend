"use strict";
/**
 * Care Reality Engine Foundation — process all phases into one enrichment layer.
 * Wired from situation-entry. Does not replace ACS/SRE — strengthens them.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.processCareRealityEngineFoundation = processCareRealityEngineFoundation;
const identity_attribution_1 = require("./identity-attribution");
const baseline_profile_1 = require("./baseline-profile");
const core_objects_1 = require("./core-objects");
const change_detection_1 = require("./change-detection");
const behavioral_observation_1 = require("./behavioral-observation");
const capacity_adaptation_1 = require("./capacity-adaptation");
const care_transition_1 = require("./care-transition");
const safety_boundary_1 = require("./safety-boundary");
const orientation_validation_1 = require("./orientation-validation");
const phases_1 = require("./phases");
const unknowns_engine_1 = require("../unknowns-engine");
const care_reality_extraction_1 = require("../care-reality-extraction");
function processCareRealityEngineFoundation(input) {
    const now = input.as_of ?? new Date().toISOString();
    const combinedText = [input.raw_input, ...(input.document_texts ?? [])]
        .filter(Boolean)
        .join("\n");
    // Phase 1 — Identity
    const identity = (0, identity_attribution_1.resolveIdentityAttribution)({
        careRecipientId: input.care_recipient_id,
        contributorId: input.contributor_id,
        rawText: combinedText,
        nowIso: now,
    });
    // Phase 2 — Baseline
    if (input.baseline_facts?.length) {
        (0, baseline_profile_1.syncBaselineFromIntelligenceFacts)({
            careRecipientId: input.care_recipient_id,
            facts: input.baseline_facts,
            nowIso: now,
        });
    }
    const baseline_profile = (0, baseline_profile_1.getBaselineProfile)(input.care_recipient_id);
    // Phase 3 — Core objects (from this turn's understanding + extraction)
    const core = (0, core_objects_1.emptyCoreBundle)();
    const eventIds = input.event_ids ?? [];
    if (input.raw_input.trim() || (input.document_texts?.length ?? 0) > 0) {
        const evt = {
            id: eventIds[0] ?? `cre_${Date.now()}`,
            type: "care_reality_update",
            date: now,
            description: (input.what_is_happening ?? input.raw_input).trim().slice(0, 500),
            source: (input.document_texts?.length ?? 0) > 0 ? "document+text" : "text",
            related_situation_id: null,
            contributor_id: input.contributor_id,
        };
        core.events.push(evt);
        const obs = (0, behavioral_observation_1.preserveBehavioralObservation)({
            id: `obs_${evt.id}`,
            rawDescription: input.raw_input.trim() || evt.description,
            contributorId: input.contributor_id,
            date: now,
            source: "caregiver",
        });
        if (obs) {
            const careObs = {
                id: obs.id,
                description: obs.description,
                contributor_id: obs.contributor_id,
                date: obs.date,
                confidence: "medium",
                source: obs.source,
                related_situation_id: null,
            };
            core.observations.push(careObs);
        }
        // Enrich with extraction — Action ≠ Outcome; Decision keeps why/unknown why
        const extracted = (0, care_reality_extraction_1.extractCareRealityFromText)({
            rawText: combinedText,
            contributorId: input.contributor_id,
            source: "caregiver",
        });
        for (const d of extracted.decisions.slice(0, 5)) {
            core.decisions.push({
                id: d.id,
                decision: d.description,
                date: now,
                participants: d.who,
                reason: d.why,
                evidence: d.evidence_texts,
                alternatives: d.alternatives,
                outcome: d.outcome,
                status: d.reason_unknown ? "unknown" : "active",
            });
        }
        for (const a of extracted.actions.slice(0, 5)) {
            core.actions.push({
                id: a.id,
                description: a.description,
                who: a.who,
                date: now,
                source: a.source,
                contributor_id: input.contributor_id,
                related_situation_id: null,
                related_decision_id: a.related_decision_id,
            });
        }
        for (const o of extracted.outcomes.slice(0, 5)) {
            core.outcomes.push({
                id: o.id,
                result: o.description,
                date: now,
                evidence: o.evidence_texts,
                related_decision_id: o.related_type === "decision" ? o.related_id : null,
                related_situation_id: null,
            });
        }
        for (const e of extracted.events.slice(0, 5)) {
            if (core.events.some((x) => x.description === e.description))
                continue;
            core.events.push({
                id: e.id,
                type: "extracted_event",
                date: now,
                description: e.description,
                source: "text",
                related_situation_id: null,
                contributor_id: input.contributor_id,
            });
        }
    }
    // Unknowns — never invent answers; preserve gaps
    try {
        const derived = (0, unknowns_engine_1.deriveExplicitUnknowns)({
            known: [input.what_is_happening ?? ""].filter(Boolean),
            inferred: [],
            event_texts: [combinedText].filter(Boolean),
            unresolved_clarifications: input.what_is_uncertain ?? [],
            related_care_event_ids: eventIds,
        });
        for (const u of derived.explicit_unknowns.slice(0, 5)) {
            core.unknowns.push({
                id: u.unknown_id,
                question: u.clarification_question || u.missing_information,
                why_it_matters: u.why_it_matters || u.reason_it_matters,
                related_situation_id: null,
            });
        }
    }
    catch {
        /* profile-driven unknowns are optional enrichment */
    }
    if (core.unknowns.length === 0) {
        for (const q of input.what_is_uncertain ?? []) {
            if (!q.trim())
                continue;
            core.unknowns.push({
                id: `unk_${core.unknowns.length}`,
                question: q.trim(),
                why_it_matters: "Reduces uncertainty in the care reality.",
                related_situation_id: null,
            });
        }
    }
    // Phase 6 — Change detection
    const whatChangedList = Array.isArray(input.what_changed)
        ? input.what_changed
        : input.what_changed
            ? [input.what_changed]
            : [];
    const changes = (0, change_detection_1.detectChangesFromComparison)({
        priorSummaries: baseline_profile?.entries.map((e) => e.summary) ?? [],
        currentSummaries: [
            input.what_is_happening ?? "",
            ...whatChangedList,
        ].filter(Boolean),
        deviations: input.baseline_deviations,
        conflictNote: input.conflict_note,
        evidenceIds: eventIds,
    });
    // Phase 9 — Capacity
    const capacity = (0, capacity_adaptation_1.adaptForCaregiverCapacity)(combinedText);
    // Phase 10 — Transitions
    const transitions = (0, care_transition_1.detectCareTransitions)(combinedText);
    // Phase 11 — Safety
    let safety = null;
    if (input.final_output) {
        safety = (0, safety_boundary_1.applySafetyBoundaryToOutput)(input.final_output, input.risk_level);
    }
    // Phase 13 — Orientation validation
    const orientation = (0, orientation_validation_1.validateCaregiverOrientation)({
        what_is_happening: input.what_is_happening,
        what_changed: whatChangedList[0] ?? null,
        what_matters_now: input.what_matters_now,
        what_remains_uncertain: input.what_is_uncertain,
        what_to_ask_next: input.what_to_ask_next,
        what_can_wait: input.what_can_wait,
    });
    const recipient_clarification_invite = identity.needs_recipient_clarification
        ? "What name should we use for the person receiving care?"
        : null;
    return {
        phases_completed: [...phases_1.CARE_REALITY_ENGINE_PHASES],
        identity,
        baseline_profile,
        core,
        changes,
        capacity,
        transitions,
        safety,
        orientation,
        recipient_clarification_invite,
    };
}
