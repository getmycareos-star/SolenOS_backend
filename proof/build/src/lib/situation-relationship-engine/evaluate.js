"use strict";
/**
 * Situation Relationship Engine (MVP)
 * Product SoT: docs/02-product/solenos-situation-relationship-directive.md
 *              docs/02-product/solenos-mvp-situation-relationship-architecture.md
 *
 * Evaluates whether new input updates / relates / answers / opens new —
 * before CareEvents are appended. Not keyword note piles. Not a graph UI.
 *
 * Ordered signals: time → same recipient → topic/underlying issue →
 * answers uncertainty → strengthens pattern → new decision (linked, not merged).
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.SITUATION_RELATIONSHIP_ENGINE_PURPOSE = void 0;
exports.findReinforcementTargetObservation = findReinforcementTargetObservation;
exports.evaluateSituationRelationship = evaluateSituationRelationship;
const detect_signals_1 = require("../progressive-understanding/detect-signals");
const signals_1 = require("./signals");
exports.SITUATION_RELATIONSHIP_ENGINE_PURPOSE = "Evaluate how new input relates to existing care reality before creating or linking CareEvents.";
function normalizeForCompare(text) {
    return text
        .toLowerCase()
        .replace(/\b(just reminding you|reminder|again|also|update)\b/gi, " ")
        .replace(/[^a-z0-9\s]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
}
/**
 * Observation that incoming text restates — used by ACS ingest to merge spine events
 * without appending a duplicate timeline row.
 */
function findReinforcementTargetObservation(active, rawText) {
    const incoming = normalizeForCompare(rawText);
    if (incoming.length < 8)
        return null;
    const reminder = /\b(remind|reminding|same as|as i (said|told)|already (said|told))\b/i.test(rawText);
    for (const obs of active.observations) {
        const prior = normalizeForCompare(obs.raw_text);
        if (!prior)
            continue;
        if (incoming === prior)
            return obs;
        if (reminder && (incoming.includes(prior.slice(0, 24)) || prior.includes(incoming.slice(0, 24)))) {
            return obs;
        }
        const a = new Set(incoming.split(" ").filter((w) => w.length > 3));
        const b = new Set(prior.split(" ").filter((w) => w.length > 3));
        if (a.size === 0 || b.size === 0)
            continue;
        let overlap = 0;
        for (const w of a)
            if (b.has(w))
                overlap += 1;
        const ratio = overlap / Math.min(a.size, b.size);
        if (ratio >= 0.85 && overlap >= 3)
            return obs;
    }
    return null;
}
function looksLikeReinforcement(active, rawText) {
    return findReinforcementTargetObservation(active, rawText) !== null;
}
function softActive(active) {
    return (active.theme === "emotional_behavior" ||
        active.observations.every((o) => (0, signals_1.isSoftObservationKind)(o.kind)));
}
/**
 * Core engine: evaluate new input against Active Care Situation + Care Reality context.
 * Does not invent diagnosis. Does not expose enums to caregivers.
 */
function evaluateSituationRelationship(params) {
    const { active, rawText, kind, nowIso } = params;
    if (!active) {
        return {
            decision: "NEW_UNRELATED_SITUATION",
            acs_relation: "opens_new",
            related_situation_id: null,
            reason: "No active care situation — open a new thread for this input.",
            confidence: "high",
            is_reinforcement: false,
            identity_mismatch: false,
            is_improvement_outcome: false,
        };
    }
    // Signal 2 — same care recipient (explicit kinship conflict).
    if ((0, signals_1.hasExplicitIdentityConflict)(active.subject_label, rawText)) {
        return {
            decision: "UNCERTAIN_NEEDS_REVIEW",
            acs_relation: "opens_new",
            related_situation_id: null,
            reason: "Incoming note appears to refer to a different care recipient than the active situation.",
            confidence: "high",
            is_reinforcement: false,
            identity_mismatch: true,
            is_improvement_outcome: false,
        };
    }
    // Signal 5 — strengthens / restates existing observation.
    if (looksLikeReinforcement(active, rawText)) {
        return {
            decision: "REINFORCE_EXISTING",
            acs_relation: "updates_active",
            related_situation_id: active.id,
            reason: "Input restates an observation already held — reinforce, do not duplicate.",
            confidence: "high",
            is_reinforcement: true,
            identity_mismatch: false,
            is_improvement_outcome: false,
        };
    }
    // Hard incident while soft ACS open → new situation (do not merge).
    // Must run before uncertainty-answer absorb: timing words like "yesterday" in a
    // fall note must not answer a soft emotional gather ask (ACS verify / Clarity bleed).
    {
        const softIncomingPreview = (0, signals_1.isSoftObservationKind)(kind) || (0, signals_1.isEmotionalOrBehavioralText)(rawText);
        const activeSoftPreview = softActive(active);
        const topicPreview = (0, signals_1.continuesUnderlyingIssue)(active, rawText);
        if ((0, signals_1.isHardEventKind)(kind) &&
            !softIncomingPreview &&
            activeSoftPreview &&
            !topicPreview) {
            return {
                decision: "NEW_UNRELATED_SITUATION",
                acs_relation: "opens_new",
                related_situation_id: null,
                reason: "Hard incident opens a new situation rather than absorbing into a soft thread.",
                confidence: "high",
                is_reinforcement: false,
                identity_mismatch: false,
                is_improvement_outcome: false,
            };
        }
    }
    // Signal 4 — answers an open uncertainty → update same.
    if ((0, signals_1.answersOpenUncertaintyGap)(active, rawText)) {
        return {
            decision: "ANSWER_PREVIOUS_UNCERTAINTY",
            acs_relation: "answers_uncertainty",
            related_situation_id: active.id,
            reason: "Input appears to answer an open uncertainty on the active situation.",
            confidence: "high",
            is_reinforcement: false,
            identity_mismatch: false,
            is_improvement_outcome: false,
        };
    }
    const softIncoming = (0, signals_1.isSoftObservationKind)(kind) || (0, signals_1.isEmotionalOrBehavioralText)(rawText);
    const improvement = (0, detect_signals_1.looksLikeImprovementNote)(rawText);
    const activeSoft = softActive(active);
    // Signal 1 — time relationship.
    const inWindow = (0, signals_1.sameCalendarDay)(active.updated_at, nowIso) ||
        (0, signals_1.withinActiveWindow)(active.updated_at, nowIso);
    const topicContinuity = (0, signals_1.continuesUnderlyingIssue)(active, rawText);
    const activeIsIncident = active.theme === "incident" ||
        active.observations.some((o) => (0, signals_1.isHardEventKind)(o.kind));
    // Signal 6 — new decision → linked related event (not merged into spine fact).
    if (inWindow && (0, signals_1.looksLikeCareDecision)(rawText)) {
        return {
            decision: "ADD_RELATED_EVENT",
            acs_relation: "adds_context",
            related_situation_id: active.id,
            reason: "Care decision linked to the active situation as a related event — not merged.",
            confidence: "high",
            is_reinforcement: false,
            identity_mismatch: false,
            is_improvement_outcome: false,
        };
    }
    // Improvement while soft ACS open → related outcome (G2), not resolve.
    if (improvement && inWindow && (activeSoft || topicContinuity)) {
        return {
            decision: "ADD_RELATED_EVENT",
            acs_relation: "adds_context",
            related_situation_id: active.id,
            reason: "Improvement observation linked to the active situation as related outcome evidence.",
            confidence: "high",
            is_reinforcement: false,
            identity_mismatch: false,
            is_improvement_outcome: true,
        };
    }
    // Soft outcome / safety detail after hard incident → continue same situation.
    // Emotional mood notes that only share place tokens (hospital waiting room) must
    // NOT glue onto an incident ACS — anti–Clarity bleed (ACS trap fixtures).
    if (inWindow && activeIsIncident && ((0, signals_1.referencesHardEventInText)(rawText) || topicContinuity)) {
        if (softIncoming &&
            (0, signals_1.isEmotionalOrBehavioralText)(rawText) &&
            !(0, signals_1.referencesHardEventInText)(rawText)) {
            return {
                decision: "NEW_UNRELATED_SITUATION",
                acs_relation: "opens_new",
                related_situation_id: null,
                reason: "Emotional observation after a hard incident opens a new situation unless it references the incident.",
                confidence: "high",
                is_reinforcement: false,
                identity_mismatch: false,
                is_improvement_outcome: false,
            };
        }
        if ((0, signals_1.answersOpenUncertaintyGap)(active, rawText)) {
            return {
                decision: "ANSWER_PREVIOUS_UNCERTAINTY",
                acs_relation: "answers_uncertainty",
                related_situation_id: active.id,
                reason: "Follow-up answers an open uncertainty on the incident.",
                confidence: "high",
                is_reinforcement: false,
                identity_mismatch: false,
                is_improvement_outcome: false,
            };
        }
        return {
            decision: "UPDATE_EXISTING_SITUATION",
            acs_relation: softIncoming ? "updates_active" : "adds_context",
            related_situation_id: active.id,
            reason: topicContinuity
                ? "New input continues the underlying issue of the active situation."
                : "Follow-up references the active incident — continue the same situation.",
            confidence: "high",
            is_reinforcement: false,
            identity_mismatch: false,
            is_improvement_outcome: false,
        };
    }
    // Soft → soft/mixed same window: update understanding.
    // Soft after hard without topic continuity stays new (anti–Clarity bleed).
    // Soft after hard WITH topic continuity (fall → afraid to walk) updates same.
    if (softIncoming && inWindow) {
        if (!activeSoft && !(0, signals_1.referencesHardEventInText)(rawText) && !topicContinuity) {
            return {
                decision: "NEW_UNRELATED_SITUATION",
                acs_relation: "opens_new",
                related_situation_id: null,
                reason: "Soft observation should not absorb into an unrelated hard Active Care Situation.",
                confidence: "medium",
                is_reinforcement: false,
                identity_mismatch: false,
                is_improvement_outcome: false,
            };
        }
        return {
            decision: "UPDATE_EXISTING_SITUATION",
            acs_relation: "updates_active",
            related_situation_id: active.id,
            reason: topicContinuity
                ? "Related observation continues the underlying care issue — update understanding."
                : "Related soft update expands the active care situation.",
            confidence: "high",
            is_reinforcement: false,
            identity_mismatch: false,
            is_improvement_outcome: false,
        };
    }
    // Related care-change after incident (e.g. med change / visit after fall) → linked event.
    if (inWindow &&
        active.theme === "incident" &&
        (kind === "medication_change" ||
            kind === "appointment" ||
            kind === "hospital_discharge" ||
            kind === "document")) {
        return {
            decision: "ADD_RELATED_EVENT",
            acs_relation: "adds_context",
            related_situation_id: active.id,
            reason: "Care-change event appears connected to the active incident situation.",
            confidence: "medium",
            is_reinforcement: false,
            identity_mismatch: false,
            is_improvement_outcome: false,
        };
    }
    // Hard incident while soft ACS open → new situation (do not merge), unless same topic.
    if ((0, signals_1.isHardEventKind)(kind) && !softIncoming && activeSoft && !topicContinuity) {
        return {
            decision: "NEW_UNRELATED_SITUATION",
            acs_relation: "opens_new",
            related_situation_id: null,
            reason: "Hard incident opens a new situation rather than absorbing into a soft thread.",
            confidence: "high",
            is_reinforcement: false,
            identity_mismatch: false,
            is_improvement_outcome: false,
        };
    }
    if ((0, signals_1.isHardEventKind)(kind) && !softIncoming && !inWindow) {
        return {
            decision: "NEW_UNRELATED_SITUATION",
            acs_relation: "opens_new",
            related_situation_id: null,
            reason: "Hard event outside the active window — treat as a distinct situation.",
            confidence: "medium",
            is_reinforcement: false,
            identity_mismatch: false,
            is_improvement_outcome: false,
        };
    }
    if ((0, signals_1.isHardEventKind)(kind) && !softIncoming && inWindow && active.theme === "incident") {
        return {
            decision: "NEW_UNRELATED_SITUATION",
            acs_relation: "opens_new",
            related_situation_id: null,
            reason: "Distinct hard event — open a new situation rather than merge incidents.",
            confidence: "medium",
            is_reinforcement: false,
            identity_mismatch: false,
            is_improvement_outcome: false,
        };
    }
    // Topic continuity outside softIncoming path (e.g. mixed kinds).
    if (inWindow && topicContinuity) {
        return {
            decision: "UPDATE_EXISTING_SITUATION",
            acs_relation: "updates_active",
            related_situation_id: active.id,
            reason: "Input continues the underlying issue of the active care reality.",
            confidence: "medium",
            is_reinforcement: false,
            identity_mismatch: false,
            is_improvement_outcome: false,
        };
    }
    return {
        decision: "NEW_UNRELATED_SITUATION",
        acs_relation: "opens_new",
        related_situation_id: null,
        reason: "Insufficient continuity signal — open a new situation.",
        confidence: "low",
        is_reinforcement: false,
        identity_mismatch: false,
        is_improvement_outcome: false,
    };
}
