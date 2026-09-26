"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.RETENTION_MICRO_PROMPT_STATUS = void 0;
exports.weekKeyFromIso = weekKeyFromIso;
exports.getRetentionResearchStore = getRetentionResearchStore;
exports.deriveRetentionProxySignals = deriveRetentionProxySignals;
exports.recordRetentionResearchEvent = recordRetentionResearchEvent;
exports.aggregateWeeklyRetentionCohortMetrics = aggregateWeeklyRetentionCohortMetrics;
exports.resetRetentionResearchStore = resetRetentionResearchStore;
exports.attachFeedbackToRetentionResearch = attachFeedbackToRetentionResearch;
/**
 * Slice 5.6 — Retention hypothesis instrumentation (ops / research only).
 *
 * Four MVP research questions — never a caregiver survey wall, never engagement hacks.
 * Proxies from Living Care Record behavior: orientation, return, explainability, change-return.
 *
 * SoT: docs/02-product/solenos-mvp-research-validation.md
 * Spine: docs/17-canonical-architecture/spine-build-sequence.md Slice 5.6
 *
 * Quiet post-session micro-prompt UI = FUTURE (requires ADR) — not shipped here.
 */
const fs_store_1 = require("../living-care-record-persistence/fs-store");
const contract_constants_1 = require("./contract-constants");
const memory = new Map();
function filePath(careKey) {
    return (0, fs_store_1.livingCareRecordDataDir)("retention-research", `${(0, fs_store_1.sanitizeDurableCareKey)(careKey)}.json`);
}
/** ISO week key YYYY-Www (UTC) for cohort bucketing. */
function weekKeyFromIso(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) {
        return weekKeyFromIso(new Date().toISOString());
    }
    // ISO week: Thursday-based year
    const utc = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    const day = utc.getUTCDay() || 7;
    utc.setUTCDate(utc.getUTCDate() + 4 - day);
    const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
    const week = Math.ceil(((utc.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
    return `${utc.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}
function emptyStore(careKey) {
    return {
        care_key: careKey,
        events: [],
        updated_at: new Date().toISOString(),
    };
}
function getRetentionResearchStore(careKey) {
    const key = careKey.trim();
    const cached = memory.get(key);
    if (cached)
        return cached;
    const durable = (0, fs_store_1.readDurableJson)(filePath(key));
    if (durable?.events) {
        memory.set(key, durable);
        return durable;
    }
    return emptyStore(key);
}
function persist(store) {
    memory.set(store.care_key, store);
    (0, fs_store_1.writeDurableJson)(filePath(store.care_key), store);
    return store;
}
/**
 * Derive research proxies from composed orientation + session context.
 * Conservative: missing evidence → false (unknown), never invent survey answers.
 */
function deriveRetentionProxySignals(params) {
    const tags = [];
    const composed = params.composed;
    const oriented = Boolean(composed.what_changed?.trim()) ||
        Boolean(composed.situation_summary?.trim()) ||
        (composed.what_we_know?.length ?? 0) > 0;
    if (oriented)
        tags.push("orientation_surface");
    const understand_better = oriented ||
        params.reducedConfusion === true ||
        params.helpfulFeedback === true;
    if (params.reducedConfusion === true)
        tags.push("reduced_confusion_feedback");
    if (params.helpfulFeedback === true)
        tags.push("helpful_feedback");
    const less_fear_of_forgetting = params.careWorthyCount >= 1 &&
        (Boolean(composed.confirmation?.trim()) ||
            Boolean(composed.connection_note?.trim()) ||
            params.isReturn === true);
    if (params.careWorthyCount >= 1)
        tags.push("care_held");
    if (params.isReturn)
        tags.push("return_visit");
    const can_explain_better = (composed.what_we_know?.length ?? 0) >= 1 ||
        Boolean(composed.situation_summary?.trim()) ||
        Boolean(composed.what_matters_now?.trim()) ||
        params.hasDecisionWhy === true;
    if (params.hasDecisionWhy)
        tags.push("decision_why");
    if ((composed.what_we_know?.length ?? 0) >= 1)
        tags.push("held_facts");
    const changeRelation = params.relation === "updates_active" ||
        params.relation === "adds_context" ||
        params.relation === "answers_uncertainty";
    const would_return_on_change = (params.isReturn === true && (oriented || changeRelation)) ||
        (changeRelation && params.careWorthyCount >= 2);
    if (changeRelation)
        tags.push("change_update");
    return {
        signals: {
            understand_better,
            less_fear_of_forgetting,
            can_explain_better,
            would_return_on_change,
        },
        evidence_tags: tags,
    };
}
/** Record one ops research event — never surfaces in caregiver UI. */
function recordRetentionResearchEvent(params) {
    const now = params.nowIso ?? new Date().toISOString();
    const careKey = params.careKey.trim();
    const { signals, evidence_tags } = deriveRetentionProxySignals({
        composed: params.composed,
        careWorthyCount: params.careWorthyCount,
        isReturn: params.isReturn,
        relation: params.relation,
        helpfulFeedback: params.helpfulFeedback,
        reducedConfusion: params.reducedConfusion,
        hasDecisionWhy: params.hasDecisionWhy,
    });
    const event = {
        care_key: careKey,
        recorded_at: now,
        week_key: weekKeyFromIso(now),
        signals,
        evidence_tags,
        turn_class: params.turnClass ?? null,
        is_return: params.isReturn ?? false,
        relation: params.relation ?? null,
    };
    const store = getRetentionResearchStore(careKey);
    const next = {
        care_key: careKey,
        events: [...store.events, event].slice(-200),
        updated_at: now,
    };
    persist(next);
    return event;
}
function listAllStores() {
    const dir = (0, fs_store_1.livingCareRecordDataDir)("retention-research");
    for (const name of (0, fs_store_1.listDurableDirectory)(dir)) {
        if (!name.endsWith(".json"))
            continue;
        const careKey = name.replace(/\.json$/, "");
        if (!memory.has(careKey)) {
            getRetentionResearchStore(careKey);
        }
    }
    return [...memory.values()];
}
/**
 * Weekly cohort metrics for MVP research — ops only.
 * Answers whether proxies for the four hypothesis questions are present in the cohort.
 */
function aggregateWeeklyRetentionCohortMetrics(params) {
    const week = params?.weekKey ??
        weekKeyFromIso(params?.nowIso ?? new Date().toISOString());
    const stores = listAllStores();
    const careKeys = new Set();
    const events = [];
    const positiveKeys = {
        understand_what_is_happening_better: new Set(),
        less_afraid_of_forgetting_something_important: new Set(),
        can_explain_the_situation_better_to_another_person: new Set(),
        would_use_again_when_something_changes: new Set(),
    };
    const signalForHypothesis = (s, id) => {
        switch (id) {
            case "understand_what_is_happening_better":
                return s.understand_better;
            case "less_afraid_of_forgetting_something_important":
                return s.less_fear_of_forgetting;
            case "can_explain_the_situation_better_to_another_person":
                return s.can_explain_better;
            case "would_use_again_when_something_changes":
                return s.would_return_on_change;
            default:
                return false;
        }
    };
    let positiveEventCounts = {
        understand_what_is_happening_better: 0,
        less_afraid_of_forgetting_something_important: 0,
        can_explain_the_situation_better_to_another_person: 0,
        would_use_again_when_something_changes: 0,
    };
    for (const store of stores) {
        for (const ev of store.events) {
            if (ev.week_key !== week)
                continue;
            careKeys.add(ev.care_key);
            events.push(ev);
            for (const id of contract_constants_1.RESEARCH_RETENTION_HYPOTHESIS) {
                if (signalForHypothesis(ev.signals, id)) {
                    positiveEventCounts[id] += 1;
                    positiveKeys[id].add(ev.care_key);
                }
            }
        }
    }
    const n = events.length;
    const rates = {};
    const care_keys_positive = {};
    for (const id of contract_constants_1.RESEARCH_RETENTION_HYPOTHESIS) {
        rates[id] = n === 0 ? 0 : positiveEventCounts[id] / n;
        care_keys_positive[id] = positiveKeys[id].size;
    }
    return {
        week_key: week,
        cohort_care_keys: careKeys.size,
        event_count: n,
        rates,
        care_keys_positive,
        ops_only: true,
        no_caregiver_survey: true,
    };
}
function resetRetentionResearchStore() {
    memory.clear();
    (0, fs_store_1.clearDurableDirectory)((0, fs_store_1.livingCareRecordDataDir)("retention-research"));
}
/**
 * Fold relief feedback into the latest research event for this care key.
 * Ops only — never creates a caregiver survey surface.
 */
function attachFeedbackToRetentionResearch(params) {
    const store = getRetentionResearchStore(params.careKey);
    if (store.events.length === 0)
        return null;
    const last = store.events[store.events.length - 1];
    const tags = new Set(last.evidence_tags);
    if (params.helpfulFeedback)
        tags.add("helpful_feedback");
    if (params.reducedConfusion)
        tags.add("reduced_confusion_feedback");
    const signals = {
        ...last.signals,
        understand_better: last.signals.understand_better ||
            params.helpfulFeedback ||
            params.reducedConfusion,
    };
    const updated = {
        ...last,
        signals,
        evidence_tags: [...tags],
        recorded_at: params.nowIso ?? last.recorded_at,
    };
    const events = [...store.events.slice(0, -1), updated];
    persist({
        care_key: store.care_key,
        events,
        updated_at: params.nowIso ?? new Date().toISOString(),
    });
    return updated;
}
/** FUTURE — quiet post-session micro-prompt requires ADR; never ship survey wall. */
exports.RETENTION_MICRO_PROMPT_STATUS = "FUTURE_REQUIRES_ADR";
