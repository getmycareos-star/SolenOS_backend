"use strict";
/**
 * Return Continuity — G10 soft invite + G11/G18 restore projection.
 * Product SoT:
 * - docs/02-product/solenos-open-uncertainties-return.md (decision B)
 * - docs/02-product/solenos-welcome-begin-continuity.md (decision A)
 * - Golden G10 / G11 / G18
 *
 * Spine only: durable visit/invite state + projection. Not ChatGPT welcome-back.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.LONG_ABSENCE_THRESHOLD_MS = exports.SHORT_RETURN_THRESHOLD_MS = void 0;
exports.getReturnContinuityRecord = getReturnContinuityRecord;
exports.markInteractionPaused = markInteractionPaused;
exports.markInteractionTouched = markInteractionTouched;
exports.buildReturnContinuityProjection = buildReturnContinuityProjection;
exports.resetReturnContinuityStore = resetReturnContinuityStore;
exports.clearSoftInviteWhenUncertaintyGone = clearSoftInviteWhenUncertaintyGone;
const fs_store_1 = require("../living-care-record-persistence/fs-store");
const care_history_compression_1 = require("../care-history-compression");
const care_epistemics_1 = require("../care-epistemics");
/** Soft return invite after short pause (Done for now). */
exports.SHORT_RETURN_THRESHOLD_MS = 60 * 60 * 1000; // 1 hour
/** Long absence (G18) — ~90 days. */
exports.LONG_ABSENCE_THRESHOLD_MS = 90 * 24 * 60 * 60 * 1000;
const memory = new Map();
function filePath(careKey) {
    return (0, fs_store_1.livingCareRecordDataDir)("return-continuity", `${(0, fs_store_1.sanitizeDurableCareKey)(careKey)}.json`);
}
function emptyRecord(careKey) {
    return {
        care_key: careKey,
        last_interaction_at: null,
        last_paused_at: null,
        soft_invite_offered_at: null,
        soft_invite_uncertainty: null,
        updated_at: new Date().toISOString(),
    };
}
function getReturnContinuityRecord(careKey) {
    const cached = memory.get(careKey);
    if (cached)
        return cached;
    const durable = (0, fs_store_1.readDurableJson)(filePath(careKey));
    if (durable) {
        memory.set(careKey, durable);
        return durable;
    }
    return emptyRecord(careKey);
}
function persist(record) {
    memory.set(record.care_key, record);
    (0, fs_store_1.writeDurableJson)(filePath(record.care_key), record);
    return record;
}
/** Done for now — pause interaction; open uncertainties remain. */
function markInteractionPaused(careKey, nowIso) {
    const now = nowIso ?? new Date().toISOString();
    const prior = getReturnContinuityRecord(careKey);
    return persist({
        ...prior,
        care_key: careKey,
        last_paused_at: now,
        last_interaction_at: now,
        updated_at: now,
    });
}
/** Capture / Begin resume — touch last interaction without consuming invite. */
function markInteractionTouched(careKey, nowIso) {
    const now = nowIso ?? new Date().toISOString();
    const prior = getReturnContinuityRecord(careKey);
    return persist({
        ...prior,
        care_key: careKey,
        last_interaction_at: now,
        last_paused_at: null,
        updated_at: now,
    });
}
function pickOpenUncertainty(params) {
    const fromAcs = params.acs?.open_questions?.find((q) => q.trim().length > 0)?.trim();
    if (fromAcs)
        return fromAcs;
    const fromCrs = params.crs?.open_uncertainties?.find((q) => q.trim().length > 0)?.trim();
    return fromCrs ?? null;
}
function composeSoftInviteText(params) {
    const who = params.subjectLabel === "Your loved one" || params.subjectLabel === "they"
        ? "their"
        : `${params.subjectLabel}'s`;
    const theme = params.themeHint
        ? `Last time you were documenting ${who} ${params.themeHint}. `
        : "";
    return `${theme}One question is still open: ${params.uncertainty} You can answer now or continue updating the record.`;
}
function themeHint(acs) {
    if (!acs)
        return null;
    if (acs.theme === "incident")
        return "recent incident";
    if (acs.theme === "emotional_behavior")
        return "recent changes";
    if (acs.theme === "care_change")
        return "care change";
    return "care situation";
}
function recentChanges(acs, crs) {
    if (acs && acs.observations.length > 5) {
        const compressed = (0, care_history_compression_1.projectGracefulLongTermHistory)({
            observations: acs.observations.map((o) => ({
                raw_text: o.raw_text,
                human_fact: o.human_fact,
                captured_at: o.captured_at,
            })),
            familiarityStatements: (0, care_epistemics_1.listFamiliarityBaseline)(acs.caregiver_id).map((f) => f.statement),
            openUncertainties: [
                ...(acs.open_questions ?? []),
                ...(crs?.open_uncertainties ?? []),
            ],
            nowIso: acs.updated_at,
        });
        if (compressed.compression_applied) {
            return compressed.caregiver_lines.slice(0, 3);
        }
    }
    const lines = [];
    if (acs?.observations.length) {
        for (const o of acs.observations.slice(-3).reverse()) {
            const fact = (o.human_fact || o.raw_text).trim();
            if (fact)
                lines.push(fact.slice(0, 160));
        }
    }
    if (lines.length === 0 && crs?.current_understanding?.length) {
        for (const u of crs.current_understanding.slice(0, 3)) {
            if (u.trim())
                lines.push(u.trim().slice(0, 160));
        }
    }
    if (crs?.what_changed_in_understanding?.trim()) {
        lines.unshift(crs.what_changed_in_understanding.trim().slice(0, 160));
    }
    return [...new Set(lines)].slice(0, 3);
}
function historyCompressionMeta(acs, crs) {
    if (!acs || acs.observations.length <= 5) {
        return { older_compressed_count: 0, history_compression_applied: false };
    }
    const compressed = (0, care_history_compression_1.projectGracefulLongTermHistory)({
        observations: acs.observations.map((o) => ({
            raw_text: o.raw_text,
            human_fact: o.human_fact,
            captured_at: o.captured_at,
        })),
        familiarityStatements: (0, care_epistemics_1.listFamiliarityBaseline)(acs.caregiver_id).map((f) => f.statement),
        openUncertainties: [
            ...(acs.open_questions ?? []),
            ...(crs?.open_uncertainties ?? []),
        ],
        nowIso: acs.updated_at,
    });
    return {
        older_compressed_count: compressed.older_compressed_count,
        history_compression_applied: compressed.compression_applied,
    };
}
function importantUnresolved(acs, crs) {
    const lines = [
        ...(acs?.open_questions ?? []),
        ...(crs?.open_uncertainties ?? []),
    ]
        .map((q) => q.trim())
        .filter(Boolean);
    return [...new Set(lines)].slice(0, 2);
}
/**
 * Build return projection. Optionally consumes the one-time soft invite (decision B).
 * Never dumps full history. Never first-time UX when durable reality exists.
 */
function buildReturnContinuityProjection(params) {
    const now = params.nowIso ?? new Date().toISOString();
    const record = getReturnContinuityRecord(params.careKey);
    const anchor = record.last_paused_at ?? record.last_interaction_at;
    const absence_ms = anchor
        ? Math.max(0, Date.parse(now) - Date.parse(anchor))
        : 0;
    const has_durable_care_reality = Boolean(params.acs?.observations.length ||
        (params.crs &&
            (params.crs.observation_count > 0 ||
                params.crs.current_understanding.length > 0)));
    const is_long_absence = has_durable_care_reality && absence_ms >= exports.LONG_ABSENCE_THRESHOLD_MS;
    const is_return = has_durable_care_reality &&
        (Boolean(record.last_paused_at) ||
            absence_ms >= exports.SHORT_RETURN_THRESHOLD_MS ||
            is_long_absence);
    const recent_relevant_changes = is_return
        ? recentChanges(params.acs, params.crs)
        : [];
    const important_unresolved = is_return
        ? importantUnresolved(params.acs, params.crs)
        : [];
    let soft_invite = {
        offered_now: false,
        text: null,
        uncertainty: null,
    };
    const uncertainty = pickOpenUncertainty({
        acs: params.acs,
        crs: params.crs,
    });
    const alreadyOffered = Boolean(record.soft_invite_offered_at);
    if (params.offerSoftInvite &&
        is_return &&
        !alreadyOffered &&
        uncertainty &&
        Boolean(record.last_paused_at || is_long_absence)) {
        const text = composeSoftInviteText({
            subjectLabel: params.acs?.subject_label ?? params.crs?.care_recipient_label ?? "they",
            uncertainty,
            themeHint: themeHint(params.acs),
        });
        persist({
            ...record,
            care_key: params.careKey,
            soft_invite_offered_at: now,
            soft_invite_uncertainty: uncertainty,
            updated_at: now,
        });
        soft_invite = { offered_now: true, text, uncertainty };
    }
    else if (alreadyOffered && record.soft_invite_uncertainty) {
        // Already offered — do not re-show pressure text.
        soft_invite = {
            offered_now: false,
            text: null,
            uncertainty: record.soft_invite_uncertainty,
        };
    }
    return {
        is_return,
        is_long_absence,
        absence_ms,
        soft_invite,
        recent_relevant_changes,
        important_unresolved,
        suppress_first_time_ux: has_durable_care_reality,
        has_durable_care_reality,
        ...historyCompressionMeta(params.acs, params.crs),
    };
}
function resetReturnContinuityStore() {
    memory.clear();
    (0, fs_store_1.clearDurableDirectory)((0, fs_store_1.livingCareRecordDataDir)("return-continuity"));
}
/**
 * After an invited known-unknown is answered/irrelevant, clear the one-shot flag
 * so a *different* future gap can get one soft invite after the next Done for now.
 * Never clears while the invited uncertainty is still open (no re-interrogation).
 */
function clearSoftInviteWhenUncertaintyGone(params) {
    const record = getReturnContinuityRecord(params.careKey);
    const invited = record.soft_invite_uncertainty?.trim();
    if (!record.soft_invite_offered_at || !invited) {
        return record;
    }
    const stillOpen = params.openUncertainties.some((q) => q.trim().toLowerCase() === invited.toLowerCase());
    if (stillOpen)
        return record;
    return persist({
        ...record,
        soft_invite_offered_at: null,
        soft_invite_uncertainty: null,
        updated_at: new Date().toISOString(),
    });
}
