"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.clearActiveCareSituationMemoryCache = void 0;
exports.resetActiveCareSituationStore = resetActiveCareSituationStore;
exports.getActiveCareSituation = getActiveCareSituation;
exports.clearActiveCareSituation = clearActiveCareSituation;
exports.pauseActiveCareSituationSession = pauseActiveCareSituationSession;
exports.resumeActiveCareSituationSession = resumeActiveCareSituationSession;
exports.projectActiveSituationTurn = projectActiveSituationTurn;
exports.ingestActiveCareObservation = ingestActiveCareObservation;
const clarity_pillars_1 = require("../progressive-understanding/clarity-pillars");
const questions_1 = require("../progressive-understanding/questions");
const output_quality_1 = require("../output-quality");
const progressive_understanding_1 = require("../progressive-understanding");
const uncertainty_lifecycle_1 = require("../progressive-understanding/uncertainty-lifecycle");
const care_reality_state_1 = require("../care-reality-state");
const classify_1 = require("./classify");
const situation_relationship_engine_1 = require("../situation-relationship-engine");
const care_recipient_identity_1 = require("../care-recipient-identity");
const care_epistemics_1 = require("../care-epistemics");
const detect_1 = require("../thread-ingestion/detect");
const source_conflict_1 = require("../source-conflict");
const decision_memory_1 = require("../decision-memory");
const care_reality_extraction_1 = require("../care-reality-extraction");
const return_continuity_1 = require("../return-continuity");
const dementia_entry_extended_1 = require("../dementia-entry-extended");
const detect_signals_1 = require("../progressive-understanding/detect-signals");
const durable_store_1 = require("./durable-store");
Object.defineProperty(exports, "clearActiveCareSituationMemoryCache", { enumerable: true, get: function () { return durable_store_1.clearActiveCareSituationMemoryCache; } });
const multi_caregiver_context_model_1 = require("../multi-caregiver-context-model");
const disclosure_merge_1 = require("../response-contract/disclosure-merge");
const response_behavior_1 = require("../response-behavior");
const memory_correction_1 = require("../care-reality-engine/memory-correction");
const detect_memory_correction_1 = require("../care-reality-engine/detect-memory-correction");
function resetActiveCareSituationStore() {
    (0, durable_store_1.resetActiveCareSituationDurableStore)();
    (0, care_reality_state_1.resetCareRealityStateStore)();
}
function realityKey(contributorOrRealityId) {
    return (0, multi_caregiver_context_model_1.resolveCareRealityStoreKey)(contributorOrRealityId);
}
function getActiveCareSituation(contributorId) {
    const careRecipientId = realityKey(contributorId);
    const cached = (0, durable_store_1.acsCache)().get(careRecipientId);
    if (cached)
        return cached;
    let durable = (0, durable_store_1.loadActiveCareSituationFromDurable)(careRecipientId);
    if (!durable && careRecipientId !== contributorId) {
        durable = (0, durable_store_1.loadActiveCareSituationFromDurable)(contributorId);
    }
    if (!durable)
        return null;
    const normalized = {
        ...durable,
        care_recipient_id: durable.care_recipient_id ?? careRecipientId,
    };
    (0, durable_store_1.acsCache)().set(careRecipientId, normalized);
    if (!durable.care_recipient_id) {
        (0, durable_store_1.persistActiveCareSituationToDurable)(normalized);
    }
    return normalized;
}
/**
 * Test / hard-reset only — deletes ACS + CRS for a care key.
 * Never call from Done for now (solenos-done-for-now-continuity Locked A).
 */
function clearActiveCareSituation(contributorId) {
    const careRecipientId = realityKey(contributorId);
    (0, durable_store_1.acsCache)().delete(careRecipientId);
    (0, durable_store_1.deleteActiveCareSituationDurable)(careRecipientId);
    if (careRecipientId !== contributorId) {
        (0, durable_store_1.acsCache)().delete(contributorId);
        (0, durable_store_1.deleteActiveCareSituationDurable)(contributorId);
    }
    (0, care_reality_state_1.clearCareRealityState)(careRecipientId);
    if (careRecipientId !== contributorId) {
        (0, care_reality_state_1.clearCareRealityState)(contributorId);
    }
}
/**
 * Done for now — pause interaction session only.
 * Persists ACS + CRS unchanged. Does not resolve or quiet the care situation —
 * lifecycle is engine/evidence-owned (solenos-done-for-now-continuity Locked A).
 */
function pauseActiveCareSituationSession(contributorId) {
    const current = getActiveCareSituation(contributorId);
    if (!current)
        return null;
    const careRecipientId = current.care_recipient_id ?? realityKey(contributorId);
    const paused = {
        ...current,
        care_recipient_id: careRecipientId,
        interaction_paused_at: new Date().toISOString(),
    };
    (0, durable_store_1.acsCache)().set(careRecipientId, paused);
    (0, durable_store_1.persistActiveCareSituationToDurable)(paused);
    return paused;
}
/** Clear interaction pause when caregiver adds a new note — does not mutate lifecycle. */
function resumeActiveCareSituationSession(contributorId) {
    const current = getActiveCareSituation(contributorId);
    if (!current?.interaction_paused_at)
        return;
    const careRecipientId = current.care_recipient_id ?? realityKey(contributorId);
    const resumed = {
        ...current,
        care_recipient_id: careRecipientId,
        interaction_paused_at: null,
    };
    (0, durable_store_1.acsCache)().set(careRecipientId, resumed);
    (0, durable_store_1.persistActiveCareSituationToDurable)(resumed);
}
const EMPTY_RESPONSE_EVOLUTION = {
    updates_active_situation: false,
    answers_previous_uncertainty: false,
    strengthens_existing_hypothesis: false,
    introduces_new_pattern: false,
    changes_what_matters_now: false,
    invalidates_previous_understanding: false,
};
function attachCareRealityFields(base, crs) {
    if (crs) {
        const disclosure_plan = (0, care_reality_state_1.buildDisclosurePlan)(crs.disclosure_stage);
        return {
            ...base,
            // Disclosure owns ask count — never dump the full open-question queue.
            what_needs_context: base.what_needs_context.slice(0, disclosure_plan.max_questions),
            care_reality_state_id: crs.id,
            crs_observation_count: crs.observation_count,
            crs_revision: crs.revision,
            disclosure_stage: crs.disclosure_stage,
            disclosure_plan,
            response_evolution: crs.response_evolution,
            primary_screen_question: crs.primary_screen_question,
        };
    }
    const disclosure_stage = "early";
    const obs = base.situation.observations.length;
    const disclosure_plan = (0, care_reality_state_1.buildDisclosurePlan)(disclosure_stage);
    return {
        ...base,
        what_needs_context: base.what_needs_context.slice(0, disclosure_plan.max_questions),
        care_reality_state_id: null,
        crs_observation_count: obs,
        crs_revision: Math.max(1, obs),
        disclosure_stage,
        disclosure_plan,
        response_evolution: EMPTY_RESPONSE_EVOLUTION,
        primary_screen_question: (0, care_reality_state_1.primaryScreenQuestionFor)(disclosure_stage),
    };
}
function newId(prefix) {
    return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}
function dedupeUncertaintyLines(lines) {
    const out = [];
    for (const line of lines) {
        const t = line.trim();
        if (!t)
            continue;
        if (out.some((x) => x.toLowerCase() === t.toLowerCase()))
            continue;
        out.push(t);
    }
    return out;
}
function getPreferredSubject(priorLabel, detected, careKey) {
    const durable = (0, care_recipient_identity_1.resolveSubjectLabel)({ careKey });
    if (durable !== "Your loved one" && durable !== "they")
        return durable;
    return priorLabel ?? detected;
}
function turnFromProgressive(situation, relation, progressive, crs) {
    const attached = attachCareRealityFields({
        relation,
        situation,
        confirmation_title: progressive.confirmation_title,
        confirmation_body: progressive.confirmation_body,
        understanding_heading: progressive.understanding_heading,
        understanding_stage: progressive.understanding_stage,
        current_understanding: progressive.current_understanding,
        insufficiency_note: progressive.insufficiency_note,
        connection_note: progressive.connection_note,
        what_needs_context: progressive.open_questions,
        what_will_be_remembered: progressive.what_will_be_remembered,
        what_seems_happening: progressive.synthesis,
        what_matters_now: progressive.what_matters_now,
        show_attention_sections: progressive.show_attention_sections,
        what_changed_in_understanding: progressive.what_changed_in_understanding,
        understanding_effect: progressive.effect,
        resolved_uncertainties: progressive.resolved_uncertainties,
        pattern_label: progressive.pattern_label,
        what_can_wait: progressive.what_can_wait,
        what_may_become_serious: progressive.what_may_become_serious,
        compound_signal: progressive.compound_signal,
        trajectory_by_domain: progressive.trajectory_by_domain,
    }, crs);
    const latestObs = situation.observations[situation.observations.length - 1];
    const latestRaw = latestObs?.raw_text ?? "";
    const latestKind = (latestObs?.kind ?? "general");
    const turnClass = (0, response_behavior_1.classifyCaregiverTurn)({
        latestRawText: latestRaw,
        kind: latestKind,
        turn: attached,
        hasDocuments: false,
    });
    const relief = (0, response_behavior_1.resolveReliefDecisionForTurn)({
        turn: attached,
        turnClass,
        latestRawText: latestRaw,
    });
    const reliefPlan = (0, disclosure_merge_1.applyReliefFieldsToDisclosurePlan)({
        crsPlan: attached.disclosure_plan,
        relief,
    });
    // Relief tree owns Clarity — CRS stage plan cannot unlock pillars early.
    const showClarity = reliefPlan.show_what_matters_now;
    let what_matters_now = showClarity ? attached.what_matters_now : null;
    let what_can_wait = showClarity ? attached.what_can_wait : null;
    let what_may_become_serious = showClarity
        ? attached.what_may_become_serious
        : null;
    if (showClarity && !what_matters_now) {
        const signals = situation.observations.flatMap((o) => (0, detect_signals_1.detectObservationSignals)(o.raw_text, o.kind));
        const pillars = (0, clarity_pillars_1.buildCareClarityPillars)({
            situation,
            stage: attached.understanding_stage,
            signals,
            latestSignals: signals,
            patternLabel: progressive.pattern_label,
            kind: (situation.observations[situation.observations.length - 1]?.kind ??
                "general"),
        });
        what_matters_now =
            pillars.what_matters_now ||
                (0, output_quality_1.buildMattersNowOrientation)({
                    subjectLabel: situation.subject_label,
                    heldFocus: (0, clarity_pillars_1.heldFocusLines)(situation, 1)[0] ?? null,
                    baselineChange: null,
                    topUnknown: attached.what_needs_context[0] ?? null,
                    patternContinues: situation.observations.length >= 3,
                });
        what_can_wait =
            pillars.what_can_wait ||
                "Explaining every detail or answering questions you do not know yet.";
        what_may_become_serious = pillars.what_may_become_serious;
    }
    return {
        ...attached,
        disclosure_plan: reliefPlan,
        what_seems_happening: attached.disclosure_plan.show_situation_summary
            ? attached.what_seems_happening
            : null,
        what_matters_now,
        what_can_wait,
        what_may_become_serious,
        show_attention_sections: showClarity && (0, questions_1.understandingSufficient)({ situation }),
    };
}
/** Project a stored ACS into a caregiver turn without mutating the store. */
function projectActiveSituationTurn(situation, relation) {
    const resolved = relation ??
        (situation.observations.length <= 1 ? "opens_new" : "updates_active");
    const latest = situation.observations[situation.observations.length - 1];
    const crsKey = situation.care_recipient_id ??
        realityKey(situation.caregiver_id);
    if (!latest) {
        const crs = (0, care_reality_state_1.getCareRealityState)(crsKey);
        return attachCareRealityFields({
            relation: resolved,
            situation,
            confirmation_title: "Added to the Living Care Record",
            confirmation_body: "Added to the Living Care Record.",
            understanding_heading: "What is understood about this situation",
            understanding_stage: situation.understanding_stage,
            current_understanding: [],
            insufficiency_note: "More context would help explain why.",
            connection_note: situation.connection_note,
            what_needs_context: situation.open_questions,
            what_will_be_remembered: ["Care timeline continuity"],
            what_seems_happening: situation.synthesis,
            what_matters_now: situation.what_matters_now,
            show_attention_sections: situation.understanding_stage === "synthesizing",
            what_changed_in_understanding: situation.last_understanding_delta ?? null,
            understanding_effect: situation.last_understanding_effect ?? "opens_situation",
            resolved_uncertainties: [],
            pattern_label: situation.pattern_label ?? null,
            what_can_wait: "Explaining every detail tonight.",
            what_may_become_serious: null,
        }, crs);
    }
    const progressive = (0, progressive_understanding_1.processProgressiveUnderstanding)({
        prior: situation.observations.length <= 1
            ? null
            : {
                ...situation,
                observations: situation.observations.slice(0, -1),
            },
        relation: resolved,
        observation: latest,
        kind: latest.kind,
        rawText: latest.raw_text,
        draft: situation,
    });
    const baseTurn = turnFromProgressive(situation, resolved, progressive, null);
    const crs = (0, care_reality_state_1.getCareRealityState)(crsKey) ??
        (0, care_reality_state_1.updateCareRealityState)({
            caregiverId: crsKey,
            turn: baseTurn,
            situation,
            relation: resolved,
        });
    return turnFromProgressive(situation, resolved, progressive, crs);
}
/** Merge spine events onto held observation — no new ACS timeline row. */
function ingestReinforcementExisting(params) {
    const target = (0, situation_relationship_engine_1.findReinforcementTargetObservation)(params.prior, params.signalText);
    const targetId = target?.id ?? params.prior.observations[params.prior.observations.length - 1]?.id;
    const observations = params.prior.observations.map((obs) => {
        if (obs.id !== targetId || params.eventIds.length === 0)
            return obs;
        const mergedIds = [...new Set([...obs.event_ids, ...params.eventIds])];
        return { ...obs, event_ids: mergedIds };
    });
    const situation = {
        ...params.prior,
        updated_at: params.nowIso,
        observations,
    };
    (0, durable_store_1.acsCache)().set(params.careRecipientId, situation);
    (0, durable_store_1.persistActiveCareSituationToDurable)(situation);
    return projectActiveSituationTurn(situation, "updates_active");
}
/** Hold prior ACS — mismatched note is not appended until caregiver clarifies (G17). */
function ingestIdentityMismatchHold(params) {
    const ask = (0, situation_relationship_engine_1.composeIdentityMismatchAsk)(params.prior.subject_label, params.signalText);
    const base = projectActiveSituationTurn(params.prior, "updates_active");
    return {
        ...base,
        identity_mismatch: true,
        identity_mismatch_input: params.trimmed,
        connection_note: null,
        what_needs_context: [ask],
        confirmation_title: "Need a quick clarification",
        confirmation_body: "This update is saved — it will be linked to the care record once we confirm who it is about.",
        what_changed_in_understanding: null,
    };
}
/**
 * Slice 2.4 — Explicit memory correction on an open Care Reality.
 * Same path for text / document / snap / scan / upload (kind is attribution only).
 * Never silent overwrite: prior observation stays, marked disputed.
 */
function ingestMemoryCorrection(params) {
    const correctedValue = (0, detect_memory_correction_1.extractCorrectedClaimFromCorrection)(params.signalText);
    const originalValue = (params.target.human_fact || params.target.raw_text).trim();
    const correction = (0, memory_correction_1.recordMemoryCorrection)({
        careRecipientId: params.careRecipientId,
        fieldLabel: "held_observation",
        originalValue,
        correctedValue,
        correctedBy: params.contributorId,
        reason: params.trimmed.slice(0, 240),
        nowIso: params.nowIso,
    });
    const observations = params.prior.observations.map((obs) => obs.id === params.target.id
        ? { ...obs, disputed_by_correction_id: correction.id }
        : obs);
    const observation = {
        id: newId("obs"),
        raw_text: params.trimmed,
        human_fact: correctedValue,
        kind: params.kind,
        captured_at: params.nowIso,
        event_ids: params.eventIds,
        epistemic_kind: (0, care_epistemics_1.classifyEpistemicClaim)(params.signalText),
        contributor_id: params.contributorId,
        corrects_observation_id: params.target.id,
    };
    const situation = {
        ...params.prior,
        care_recipient_id: params.prior.care_recipient_id ?? params.careRecipientId,
        caregiver_id: params.contributorId,
        updated_at: params.nowIso,
        observations: [...observations, observation],
        synthesis: null,
    };
    const progressive = (0, progressive_understanding_1.processProgressiveUnderstanding)({
        prior: params.prior,
        relation: "updates_active",
        observation,
        kind: params.kind,
        rawText: params.signalText,
        draft: situation,
    });
    const correctedLine = correctedValue.endsWith(".")
        ? correctedValue
        : `${correctedValue}.`;
    const originalNeedle = originalValue.slice(0, 24).toLowerCase();
    const orientedUnderstanding = [
        correctedLine,
        ...progressive.current_understanding.filter((line) => !originalNeedle || !line.toLowerCase().includes(originalNeedle)),
    ].slice(0, 6);
    const whatChanged = "Prior understanding was corrected — earlier evidence kept as disputed.";
    (0, durable_store_1.acsCache)().set(params.careRecipientId, situation);
    (0, durable_store_1.persistActiveCareSituationToDurable)(situation);
    const progressiveOriented = {
        ...progressive,
        current_understanding: orientedUnderstanding,
        what_changed_in_understanding: whatChanged,
    };
    const baseTurn = turnFromProgressive(situation, "updates_active", progressiveOriented, null);
    const turnPatched = {
        ...baseTurn,
        memory_correction_applied: true,
        what_changed_in_understanding: whatChanged,
        current_understanding: orientedUnderstanding,
    };
    const crs = (0, care_reality_state_1.updateCareRealityState)({
        caregiverId: params.careRecipientId,
        turn: turnPatched,
        situation,
        relation: "updates_active",
        nowIso: params.nowIso,
        memory_correction: {
            record_id: correction.id,
            original_observation: originalValue,
            corrected_value: correctedValue,
        },
    });
    return {
        ...turnFromProgressive(situation, "updates_active", progressiveOriented, crs),
        memory_correction_applied: true,
        what_changed_in_understanding: whatChanged,
        current_understanding: orientedUnderstanding,
    };
}
function ingestActiveCareObservation(params) {
    const nowIso = params.nowIso ?? new Date().toISOString();
    const careRecipientId = realityKey(params.caregiverId);
    const contributorId = params.contributorId ?? params.caregiverId;
    resumeActiveCareSituationSession(params.caregiverId);
    const prior = getActiveCareSituation(params.caregiverId);
    const trimmed = params.rawText.trim();
    // Interpretation uses caregiver-facing fragment — never thread-source envelope.
    const signalText = (0, detect_1.caregiverFacingFragmentText)(trimmed) || trimmed;
    const eventIds = params.eventIds ?? [];
    // Idempotent — React re-renders must not restart / duplicate observations.
    if (prior) {
        const already = prior.observations.some((o) => o.raw_text.trim().toLowerCase() === trimmed.toLowerCase() ||
            (eventIds.length > 0 && eventIds.some((id) => o.event_ids.includes(id))));
        if (already) {
            const relation = prior.observations.length <= 1 ? "opens_new" : "updates_active";
            return projectActiveSituationTurn(prior, relation);
        }
    }
    // Slice 2.4 — explicit correction before reinforce / identity / open_new.
    // Requires held Care Reality; never invent correction theater on empty ACS.
    if (prior && (0, detect_memory_correction_1.looksLikeExplicitMemoryCorrection)(signalText)) {
        const target = (0, detect_memory_correction_1.findCorrectionTargetObservation)(prior, signalText);
        if (target) {
            return ingestMemoryCorrection({
                prior,
                target,
                trimmed,
                signalText,
                kind: params.kind,
                eventIds,
                nowIso,
                careRecipientId,
                contributorId,
            });
        }
    }
    const reinforceExisting = prior &&
        (params.isReinforcement === true ||
            params.relationshipDecision === "REINFORCE_EXISTING" ||
            (0, situation_relationship_engine_1.evaluateSituationRelationship)({
                active: prior,
                rawText: signalText,
                kind: params.kind,
                nowIso,
            }).decision === "REINFORCE_EXISTING");
    if (prior && reinforceExisting) {
        return ingestReinforcementExisting({
            prior,
            signalText,
            eventIds,
            nowIso,
            careRecipientId,
        });
    }
    const identityMismatchPending = prior &&
        (params.identityMismatch === true ||
            params.relationshipDecision === "UNCERTAIN_NEEDS_REVIEW" ||
            (0, situation_relationship_engine_1.evaluateSituationRelationship)({
                active: prior,
                rawText: signalText,
                kind: params.kind,
                nowIso,
            }).identity_mismatch);
    if (prior && identityMismatchPending) {
        return ingestIdentityMismatchHold({
            prior,
            signalText,
            trimmed,
            nowIso,
            careRecipientId,
        });
    }
    const relationRaw = params.forceRelation ??
        (0, classify_1.classifySituationRelation)({
            active: prior,
            rawText: signalText,
            kind: params.kind,
            nowIso,
        });
    /**
     * Locked B: when another contributor adds soft evidence to an open Care Reality,
     * do not replace the Active Care Situation (that recreates per-caregiver realities).
     * Same-contributor and hard incidents may still open new per Relationship Engine.
     */
    const priorContributorIds = new Set((prior?.observations ?? [])
        .map((o) => o.contributor_id)
        .filter((id) => Boolean(id)));
    if (prior?.caregiver_id)
        priorContributorIds.add(prior.caregiver_id);
    const otherContributorAdding = priorContributorIds.size > 0 && !priorContributorIds.has(contributorId);
    const relation = prior &&
        relationRaw === "opens_new" &&
        otherContributorAdding &&
        !(0, classify_1.isHardEventKind)(params.kind) &&
        prior.lifecycle_status !== "resolved" &&
        prior.lifecycle_status !== "historical"
        ? "adds_context"
        : relationRaw;
    const detected = (0, care_recipient_identity_1.resolveSubjectLabel)({
        careKey: careRecipientId,
        rawText: params.rawText,
    });
    // Prefer durable display name always when present (already inside resolveSubjectLabel).
    const subject = relation === "opens_new" || !prior
        ? detected
        : prior.subject_label === "Your loved one" || prior.subject_label === "they"
            ? detected !== "Your loved one" && detected !== "they"
                ? detected
                : getPreferredSubject(prior.subject_label, detected, careRecipientId)
            : getPreferredSubject(prior.subject_label, detected, careRecipientId);
    const epistemic = (0, care_epistemics_1.classifyEpistemicClaim)(signalText);
    // Locked B: caregiver-facing fact is the fragment — never the [thread-source] dump.
    const factSource = (0, detect_1.caregiverFacingFragmentText)(trimmed) || trimmed;
    const observation = {
        id: newId("obs"),
        raw_text: trimmed,
        human_fact: (0, classify_1.refineHumanFact)(factSource, subject, {
            isFirst: relation === "opens_new" || !prior,
        }),
        kind: params.kind,
        captured_at: nowIso,
        event_ids: eventIds,
        epistemic_kind: epistemic,
        contributor_id: contributorId,
    };
    const sourceConflict = (0, source_conflict_1.evaluateSourceConflict)({
        careKey: careRecipientId,
        priorObservations: (prior?.observations ?? []).map((o) => ({
            raw_text: o.raw_text,
            kind: o.kind,
            captured_at: o.captured_at,
            contributor_id: o.contributor_id,
        })),
        incomingText: factSource,
        incomingKind: params.kind,
        incomingCapturedAt: nowIso,
        incomingContributorId: contributorId,
    });
    (0, source_conflict_1.recordSourceClaim)({
        careKey: careRecipientId,
        rawText: factSource,
        kind: params.kind,
        capturedAt: nowIso,
    });
    const priorBaselineCount = (0, care_epistemics_1.listFamiliarityBaseline)(careRecipientId).length;
    if (epistemic === "baseline_establishment" ||
        /\b(loves?|hate[sd]?|prefers?|usually|normally|always)\b/i.test(trimmed)) {
        (0, care_epistemics_1.recordFamiliarityFromText)({
            careKey: careRecipientId,
            rawText: trimmed,
            subjectLabel: subject,
            nowIso,
        });
    }
    (0, care_epistemics_1.recordDailyLivingSignal)({
        careKey: careRecipientId,
        rawText: trimmed,
        nowIso,
    });
    const familiarityLines = (0, care_epistemics_1.listFamiliarityBaseline)(careRecipientId).map((f) => f.statement);
    const deviation = (0, care_epistemics_1.familiarityDeviationNote)({
        careKey: careRecipientId,
        rawText: trimmed,
        subjectLabel: subject,
        hadPriorBaseline: priorBaselineCount > 0,
        nowIso,
    });
    const gradual = (0, care_epistemics_1.evaluateGradualChange)(careRecipientId);
    const priorTexts = (prior?.observations ?? []).map((o) => o.raw_text);
    const fluctuation = (0, care_epistemics_1.evaluateDayFluctuation)({
        priorTexts,
        latestText: trimmed,
    });
    const personhood = (0, care_epistemics_1.evaluatePersonhoodLifeChange)({
        careKey: params.caregiverId,
        rawText: trimmed,
        subjectLabel: subject,
    });
    const preference = (0, care_epistemics_1.evaluatePreferenceRecall)({
        careKey: params.caregiverId,
        rawText: trimmed,
        subjectLabel: subject,
    });
    const safety = (0, care_epistemics_1.evaluateSafetyContinuity)({
        careKey: params.caregiverId,
        rawText: trimmed,
    });
    const unknownCause = (0, care_epistemics_1.evaluateUnknownCauseChange)({
        rawText: trimmed,
        subjectLabel: subject,
    });
    const changeVsCrisis = (0, care_epistemics_1.evaluateChangeVsCrisis)({ rawText: trimmed });
    const missedCare = (0, care_epistemics_1.evaluateCaregiverMissedCare)({ rawText: trimmed });
    const disagreeing = (0, care_epistemics_1.evaluateDisagreeingViews)({
        priorTexts,
        rawText: trimmed,
    });
    const naturalLanguage = (0, care_epistemics_1.evaluateNaturalLanguageObservation)({ rawText: trimmed });
    const continuityWorry = (0, care_epistemics_1.evaluateContinuityWorry)({
        rawText: trimmed,
        observationCount: (prior?.observations.length ?? 0) + 1,
    });
    const repeatedQuestions = (0, dementia_entry_extended_1.evaluateRepeatedQuestionPattern)({
        priorTexts,
        latestText: trimmed,
    });
    const ambiguousShift = (0, dementia_entry_extended_1.evaluateAmbiguousBehaviorShift)({ rawText: trimmed });
    const normalcy = (0, dementia_entry_extended_1.evaluateNormalcyUncertainty)({ rawText: trimmed });
    const routineDisruption = (0, dementia_entry_extended_1.evaluateRoutineDisruption)({
        rawText: trimmed,
        familiarityStatements: familiarityLines,
    });
    const situationBehind = (0, dementia_entry_extended_1.evaluateSituationBehindFact)({ rawText: trimmed });
    const careTransition = (0, dementia_entry_extended_1.evaluateCareTransition)({ rawText: trimmed });
    const roleTransition = (0, dementia_entry_extended_1.evaluateCaregiverRoleTransition)({ rawText: trimmed });
    const historical = (0, dementia_entry_extended_1.evaluateHistoricalImportance)({
        priorTexts,
        rawText: trimmed,
    });
    const milestone = (0, dementia_entry_extended_1.evaluateJourneyMilestone)({ rawText: trimmed });
    const advancedCare = (0, dementia_entry_extended_1.evaluateAdvancedCareSensitivity)({ rawText: trimmed });
    let draft;
    if (relation === "opens_new" || !prior) {
        const situationId = params.situationId ?? newId("acs");
        const rootEventId = params.rootEventId ?? eventIds[0] ?? null;
        draft = {
            id: situationId,
            care_recipient_id: careRecipientId,
            caregiver_id: contributorId,
            opened_at: nowIso,
            updated_at: nowIso,
            root_event_id: rootEventId,
            subject_label: subject,
            theme: (0, classify_1.situationThemeFor)(params.kind, params.rawText),
            observations: [observation],
            open_questions: [],
            asked_questions: [],
            understanding_stage: "gathering",
            connection_note: null,
            synthesis: null,
            what_matters_now: null,
            last_understanding_effect: null,
            last_understanding_delta: null,
            pattern_label: null,
            familiarity_baseline: familiarityLines,
        };
    }
    else {
        draft = {
            ...prior,
            care_recipient_id: prior.care_recipient_id ?? careRecipientId,
            caregiver_id: contributorId,
            updated_at: nowIso,
            root_event_id: prior.root_event_id ?? params.rootEventId ?? eventIds[0] ?? null,
            subject_label: subject,
            observations: [...prior.observations, observation],
            synthesis: null,
            what_matters_now: prior.what_matters_now,
            familiarity_baseline: familiarityLines,
        };
    }
    const progressive = (0, progressive_understanding_1.processProgressiveUnderstanding)({
        prior: relation === "opens_new" ? null : prior,
        relation,
        observation,
        kind: params.kind,
        rawText: signalText,
        draft,
    });
    const decisionSource = params.kind === "document" ? "document" : "caregiver_note";
    const sreDecisionLink = params.relationshipDecision === "ADD_RELATED_EVENT" &&
        params.isImprovementOutcome !== true;
    if ((0, decision_memory_1.looksLikeDecisionEvidence)(factSource) || sreDecisionLink) {
        (0, decision_memory_1.recordDecisionFromText)({
            careKey: careRecipientId,
            rawText: factSource,
            nowIso,
            who: [contributorId],
            situationId: draft.id,
            contextSummary: draft.subject_label ?? null,
            source: decisionSource,
            eventId: observation.event_ids[0],
            forceFromRelationshipEngine: sreDecisionLink && !(0, decision_memory_1.looksLikeDecisionEvidence)(factSource),
        });
    }
    // Care Reality extraction for Decision + Outcome + Unknown layers (any care-worthy length).
    const extractedOpenUnknownAsks = [];
    if (trimmed.length >= 40) {
        const extracted = (0, care_reality_extraction_1.extractCareRealityFromText)({
            rawText: trimmed,
            contributorId,
        });
        for (const d of extracted.decisions) {
            (0, decision_memory_1.recordDecisionFromText)({
                careKey: careRecipientId,
                rawText: d.description,
                nowIso,
                who: d.who,
                situationId: draft.id,
                contextSummary: draft.subject_label ?? null,
                source: decisionSource,
                eventId: observation.event_ids[0],
                forceFromRelationshipEngine: true,
                reason: d.why,
                reasonUnknown: d.reason_unknown,
                alternatives: d.alternatives,
                outcome: d.outcome,
                status: d.status === "needs_review"
                    ? "needs_review"
                    : d.status === "pending"
                        ? "pending"
                        : d.status === "uncertain"
                            ? "uncertain"
                            : d.reason_unknown
                                ? "needs_review"
                                : "active",
            });
        }
        for (const out of extracted.outcomes) {
            if (out.related_type !== "decision")
                continue;
            // Never invent success — store neutral observed/uncertain result text only
            (0, decision_memory_1.linkDecisionOutcome)({
                careKey: careRecipientId,
                outcomeText: out.description,
                eventId: observation.event_ids[0],
                matchTokens: out.raw_fragment
                    .toLowerCase()
                    .replace(/[^a-z0-9\s]/g, " ")
                    .split(/\s+/)
                    .filter((w) => w.length > 2),
                nowIso,
                status: out.status === "uncertain" ? "uncertain" : undefined,
            });
        }
        for (const u of extracted.unknowns) {
            if (u.status !== "open")
                continue;
            extractedOpenUnknownAsks.push(u.question);
        }
    }
    const improvementSignals = (0, detect_signals_1.detectObservationSignals)(factSource, params.kind);
    if ((0, detect_signals_1.isImprovementUpdate)(improvementSignals) || relation === "answers_uncertainty") {
        (0, decision_memory_1.linkDecisionOutcome)({
            careKey: careRecipientId,
            outcomeText: factSource,
            eventId: observation.event_ids[0],
            matchTokens: factSource
                .toLowerCase()
                .replace(/[^a-z0-9\s]/g, " ")
                .split(/\s+/)
                .filter((w) => w.length > 2),
            nowIso,
        });
    }
    const what_changed = advancedCare.note ??
        continuityWorry.note ??
        normalcy.note ??
        careTransition.note ??
        roleTransition.note ??
        milestone.note ??
        historical.note ??
        situationBehind.note ??
        personhood.note ??
        routineDisruption.note ??
        repeatedQuestions.note ??
        ambiguousShift.note ??
        sourceConflict.note ??
        missedCare.note ??
        disagreeing.note ??
        fluctuation.note ??
        safety.note ??
        preference.note ??
        (gradual.emerging ? gradual.note : null) ??
        changeVsCrisis.note ??
        unknownCause.note ??
        naturalLanguage.note ??
        deviation ??
        progressive.what_changed_in_understanding;
    const pattern_label = advancedCare.pattern_label ??
        continuityWorry.pattern_label ??
        normalcy.pattern_label ??
        careTransition.pattern_label ??
        roleTransition.pattern_label ??
        milestone.pattern_label ??
        historical.pattern_label ??
        situationBehind.pattern_label ??
        personhood.pattern_label ??
        routineDisruption.pattern_label ??
        repeatedQuestions.pattern_label ??
        ambiguousShift.pattern_label ??
        sourceConflict.pattern_label ??
        missedCare.pattern_label ??
        disagreeing.pattern_label ??
        fluctuation.pattern_label ??
        safety.pattern_label ??
        (gradual.emerging ? gradual.pattern_label : null) ??
        changeVsCrisis.pattern_label ??
        (unknownCause.is_unknown_cause_change ? "change with unknown cause" : null) ??
        naturalLanguage.pattern_label ??
        progressive.pattern_label;
    let open_questions = [...progressive.open_questions];
    let known_unknowns = [...progressive.known_unknowns];
    const newlyMintedAsks = new Set(progressive.open_questions.map((q) => q.toLowerCase()));
    const addOpenAsk = (ask) => {
        if (!open_questions.some((q) => q.toLowerCase() === ask.toLowerCase())) {
            open_questions = [ask, ...open_questions].slice(0, 3);
        }
        if (!known_unknowns.some((q) => q.toLowerCase() === ask.toLowerCase())) {
            known_unknowns = [ask, ...known_unknowns].slice(0, 8);
        }
        newlyMintedAsks.add(ask.toLowerCase());
    };
    if (unknownCause.open_ask) {
        addOpenAsk(unknownCause.open_ask);
    }
    if (situationBehind.open_ask) {
        addOpenAsk(situationBehind.open_ask);
    }
    if (ambiguousShift.open_ask) {
        addOpenAsk(ambiguousShift.open_ask);
    }
    if (sourceConflict.open_ask) {
        addOpenAsk(sourceConflict.open_ask);
    }
    for (const ask of extractedOpenUnknownAsks.slice(0, 4)) {
        addOpenAsk(ask);
    }
    const priorCrs = (0, care_reality_state_1.getCareRealityState)(careRecipientId);
    // Only reconcile PRIOR open gaps against this note — never "answer" asks minted this turn
    // (same capture would false-resolve "What else…?" via "has not" in care notes).
    const priorOpenOnly = known_unknowns.filter((q) => !newlyMintedAsks.has(q.toLowerCase()));
    const lifecycle = (0, uncertainty_lifecycle_1.reconcileOpenUncertainties)({
        situation: draft,
        openQuestions: priorOpenOnly,
        rawText: factSource,
        priorResolved: priorCrs?.resolved_uncertainties ?? [],
    });
    const newlyMintedList = known_unknowns.filter((q) => newlyMintedAsks.has(q.toLowerCase()));
    known_unknowns = dedupeUncertaintyLines([
        ...lifecycle.open,
        ...newlyMintedList,
    ]).slice(0, 8);
    open_questions = open_questions.filter((q) => known_unknowns.some((o) => o.toLowerCase() === q.toLowerCase()));
    const lifecycleResolved = lifecycle.resolved.filter((r) => !progressive.resolved_uncertainties.some((p) => p.toLowerCase() === r.toLowerCase()));
    const mergedResolved = dedupeUncertaintyLines([
        ...progressive.resolved_uncertainties,
        ...lifecycleResolved,
    ]);
    let finalRelation = mergedResolved.length > 0 && relation !== "opens_new"
        ? "answers_uncertainty"
        : relation;
    if (lifecycle.resolved.length > progressive.resolved_uncertainties.length &&
        finalRelation !== "opens_new") {
        finalRelation = "answers_uncertainty";
    }
    // Prefer higher-priority source text for orientation facts when conflicted.
    let orientedUnderstanding = [...progressive.current_understanding];
    if (sourceConflict.has_conflict) {
        const preferredText = sourceConflict.priority_for_orientation === "prior"
            ? sourceConflict.prior_text
            : sourceConflict.priority_for_orientation === "incoming"
                ? sourceConflict.incoming_text
                : null;
        if (preferredText) {
            const clinicalFact = preferredText.replace(/^\[document:[^\]]*\]\s*/i, "").trim();
            const preferredKind = sourceConflict.priority_for_orientation === "prior"
                ? "document"
                : params.kind;
            if (clinicalFact &&
                (0, source_conflict_1.sourcePriorityRank)(preferredKind, preferredText) >= 80 &&
                !orientedUnderstanding.some((l) => l.includes(clinicalFact.slice(0, 40)))) {
                orientedUnderstanding = [clinicalFact.slice(0, 160), ...orientedUnderstanding].slice(0, 4);
            }
        }
    }
    const situation = {
        ...draft,
        care_recipient_id: draft.care_recipient_id ?? careRecipientId,
        theme: progressive.theme,
        understanding_stage: progressive.understanding_stage,
        open_questions: known_unknowns,
        asked_questions: progressive.asked_questions,
        connection_note: progressive.connection_note,
        synthesis: progressive.synthesis,
        what_matters_now: progressive.what_matters_now,
        last_understanding_effect: progressive.effect,
        last_understanding_delta: what_changed,
        pattern_label,
        familiarity_baseline: familiarityLines,
    };
    (0, durable_store_1.acsCache)().set(careRecipientId, situation);
    (0, durable_store_1.persistActiveCareSituationToDurable)(situation);
    (0, return_continuity_1.clearSoftInviteWhenUncertaintyGone)({
        careKey: params.caregiverId,
        openUncertainties: known_unknowns,
    });
    if (careRecipientId !== params.caregiverId) {
        (0, return_continuity_1.clearSoftInviteWhenUncertaintyGone)({
            careKey: careRecipientId,
            openUncertainties: known_unknowns,
        });
    }
    const progressiveOriented = {
        ...progressive,
        open_questions,
        known_unknowns,
        current_understanding: orientedUnderstanding,
        resolved_uncertainties: mergedResolved,
        effect: finalRelation === "answers_uncertainty" ? "answers_uncertainty" : progressive.effect,
    };
    const baseTurn = turnFromProgressive(situation, finalRelation, progressiveOriented, null);
    const turnPatched = {
        ...baseTurn,
        what_changed_in_understanding: what_changed,
        pattern_label,
        resolved_uncertainties: mergedResolved,
        relation: finalRelation,
        understanding_effect: finalRelation === "answers_uncertainty"
            ? "answers_uncertainty"
            : baseTurn.understanding_effect,
    };
    const crs = (0, care_reality_state_1.updateCareRealityState)({
        caregiverId: careRecipientId,
        turn: turnPatched,
        situation,
        relation: finalRelation,
        nowIso,
    });
    const continuityDecision = params.continuityDecision;
    return {
        ...turnFromProgressive(situation, finalRelation, progressiveOriented, crs),
        what_changed_in_understanding: what_changed,
        pattern_label,
        resolved_uncertainties: mergedResolved,
        relation: finalRelation,
        continuity_decision: continuityDecision,
    };
}
