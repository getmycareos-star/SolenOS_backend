"use strict";
/**
 * Decision Memory — preserve what was chosen and why for care continuity.
 *
 * Value is not storing the decision alone — it is preserving why the decision
 * existed (context, evidence, alternatives, outcome, unknowns).
 *
 * Record questions answer from held evidence — never Clarity form / advice engine.
 * Decision preparation ≠ recommendation: orient caregivers; never choose for them.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.looksLikeDecisionEvidence = exports.DECISION_MEMORY_PURPOSE = void 0;
exports.listDecisionMemory = listDecisionMemory;
exports.recordDecisionFromText = recordDecisionFromText;
exports.linkDecisionOutcome = linkDecisionOutcome;
exports.answerRecordQuestion = answerRecordQuestion;
exports.composeDecisionPreparation = composeDecisionPreparation;
exports.resetDecisionMemoryStore = resetDecisionMemoryStore;
exports.clearDecisionMemoryCache = clearDecisionMemoryCache;
const fs_store_1 = require("../living-care-record-persistence/fs-store");
const multi_caregiver_context_model_1 = require("../multi-caregiver-context-model");
exports.DECISION_MEMORY_PURPOSE = "Preserve why care decisions existed — answer record questions from held evidence, never advice.";
const memory = new Map();
const STOP = new Set([
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "have",
    "has",
    "was",
    "were",
    "are",
    "her",
    "his",
    "she",
    "him",
    "they",
    "them",
    "their",
    "mom",
    "dad",
    "why",
    "taking",
    "take",
]);
function tokens(text) {
    return text
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, " ")
        .split(/\s+/)
        .filter((w) => w.length > 2 && !STOP.has(w));
}
function pathFor(careKey) {
    return (0, fs_store_1.livingCareRecordDataDir)("decision-memory", `${(0, fs_store_1.sanitizeDurableCareKey)(careKey)}.json`);
}
function migrateEntry(raw, careKey) {
    const what = (raw.what ?? raw.decision ?? "").trim();
    if (!what)
        return null;
    const when = raw.when ?? raw.recorded_at ?? new Date().toISOString();
    const evidence = Array.isArray(raw.evidence) && raw.evidence.length > 0
        ? raw.evidence.map((e) => ({
            source: e.source,
            text: e.text,
            event_id: e.event_id,
        }))
        : (raw.evidence_texts ?? []).map((text) => ({ text }));
    const evidence_texts = evidence.map((e) => e.text).filter(Boolean).length > 0
        ? evidence.map((e) => e.text)
        : (raw.evidence_texts ?? [what]);
    return {
        id: raw.id ?? `dm_${Date.now().toString(36)}`,
        care_key: raw.care_key ?? careKey,
        what,
        decision: what,
        when,
        who: Array.isArray(raw.who) ? raw.who.filter(Boolean) : [],
        context_situation_id: raw.context_situation_id ?? null,
        context_summary: raw.context_summary ?? null,
        evidence,
        alternatives: Array.isArray(raw.alternatives)
            ? raw.alternatives.filter(Boolean)
            : [],
        reason: raw.reason ?? null,
        outcome: raw.outcome ?? null,
        outcome_event_ids: Array.isArray(raw.outcome_event_ids)
            ? raw.outcome_event_ids
            : [],
        status: raw.status ?? "active",
        evidence_texts,
        recorded_at: raw.recorded_at ?? when,
        content_tokens: Array.isArray(raw.content_tokens) && raw.content_tokens.length > 0
            ? raw.content_tokens
            : tokens(what),
    };
}
function storeKey(careKey) {
    return (0, multi_caregiver_context_model_1.resolveCareRealityStoreKey)(careKey);
}
function load(careKey) {
    const id = storeKey(careKey);
    const cached = memory.get(id);
    if (cached)
        return cached;
    const durable = (0, fs_store_1.readDurableJson)(pathFor(id));
    if (durable?.entries) {
        const migrated = {
            care_key: id,
            entries: durable.entries
                .map((e) => migrateEntry(e, id))
                .filter((e) => e != null),
            updated_at: durable.updated_at ?? new Date().toISOString(),
        };
        memory.set(id, migrated);
        return migrated;
    }
    return { care_key: id, entries: [], updated_at: new Date().toISOString() };
}
function save(store) {
    memory.set(store.care_key, store);
    (0, fs_store_1.writeDurableJson)(pathFor(store.care_key), store);
}
function listDecisionMemory(careKey) {
    return [...load(careKey).entries];
}
/**
 * Extract a decision candidate from caregiver / document language.
 * Delegates to unified epistemic decision signal (`decision-signal.ts`).
 */
var decision_signal_1 = require("./decision-signal");
Object.defineProperty(exports, "looksLikeDecisionEvidence", { enumerable: true, get: function () { return decision_signal_1.looksLikeDecisionEvidence; } });
const decision_signal_2 = require("./decision-signal");
function extractAlternatives(text) {
    const out = [];
    const instead = text.match(/\b(?:instead of|rather than|as opposed to)\s+([^.,;]{3,60})/i);
    if (instead?.[1])
        out.push(instead[1].trim());
    const vs = text.match(/\b([^.,;]{3,40})\s+vs\.?\s+([^.,;]{3,40})/i);
    if (vs?.[1] && vs[2]) {
        out.push(vs[1].trim(), vs[2].trim());
    }
    const orHome = text.match(/\b(rehab(?:ilitation)?|home care|home|hospital|assisted living)\b.*\bor\b.*\b(rehab(?:ilitation)?|home care|home|hospital|assisted living)\b/i);
    if (orHome?.[1] && orHome[2] && orHome[1].toLowerCase() !== orHome[2].toLowerCase()) {
        out.push(orHome[1], orHome[2]);
    }
    return [...new Set(out.map((s) => s.replace(/\s+/g, " ").trim()).filter(Boolean))].slice(0, 4);
}
function inferStatus(text) {
    if (/\b(will|plan is|planning to|considering|might|may)\b/i.test(text)) {
        return "pending";
    }
    return "active";
}
function recordDecisionFromText(params) {
    const t = params.rawText.trim();
    if (!t)
        return null;
    if (!params.forceFromRelationshipEngine && !(0, decision_signal_2.looksLikeDecisionEvidence)(t))
        return null;
    const now = params.nowIso ?? new Date().toISOString();
    const careKey = storeKey(params.careKey);
    const reasonMatch = t.match(/\b(?:because|for|to (?:help|treat|manage)|due to)\s+([^.]{5,80})/i) ??
        null;
    const what = t.length > 140 ? `${t.slice(0, 137).trim()}…` : t;
    const evidenceText = t.slice(0, 240);
    const evidence = [
        {
            source: params.source,
            text: evidenceText,
            event_id: params.eventId,
        },
    ];
    const reasonUnknown = params.reasonUnknown === true ||
        /\b(?:can'?t remember why|don'?t know why|reason (?:is )?unknown|not sure why)\b/i.test(t);
    let reason;
    if (params.reasonUnknown === true) {
        reason = null;
    }
    else if (params.reason !== undefined) {
        reason = params.reason;
    }
    else if (reasonUnknown) {
        reason = null;
    }
    else {
        reason = reasonMatch ? reasonMatch[1].trim() : null;
    }
    const entry = {
        id: `dm_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
        care_key: careKey,
        what,
        decision: what,
        when: now,
        who: (params.who ?? []).filter(Boolean),
        context_situation_id: params.situationId ?? null,
        context_summary: params.contextSummary ?? null,
        evidence,
        alternatives: params.alternatives ?? extractAlternatives(t),
        reason,
        outcome: params.outcome !== undefined ? params.outcome : null,
        outcome_event_ids: [],
        status: params.status ?? (reason === null && reasonUnknown ? "pending" : inferStatus(t)),
        evidence_texts: [evidenceText],
        recorded_at: now,
        content_tokens: tokens(t),
    };
    const store = load(careKey);
    const next = [
        ...store.entries.filter((e) => e.what !== entry.what && e.decision !== entry.what),
        entry,
    ].slice(-24);
    save({ care_key: careKey, entries: next, updated_at: now });
    return entry;
}
/**
 * Link what happened afterward to an open decision (outcome continuity).
 */
function linkDecisionOutcome(params) {
    const store = load(params.careKey);
    if (store.entries.length === 0)
        return null;
    const outcome = params.outcomeText.trim().slice(0, 240);
    if (!outcome)
        return null;
    let target = null;
    if (params.decisionId) {
        target = store.entries.find((e) => e.id === params.decisionId) ?? null;
    }
    if (!target) {
        const qTokens = params.matchTokens && params.matchTokens.length > 0
            ? [...params.matchTokens]
            : tokens(outcome);
        let bestScore = 0;
        for (const e of store.entries) {
            if (e.status === "completed")
                continue;
            const score = overlap(e.content_tokens, qTokens);
            if (score > bestScore) {
                bestScore = score;
                target = e;
            }
        }
        if (bestScore < 1)
            target = store.entries[store.entries.length - 1] ?? null;
    }
    if (!target)
        return null;
    const changed = /\b(instead|changed (?:to|from)|switched|no longer|reversed)\b/i.test(outcome);
    const nextStatus = params.status ?? (changed ? "changed" : "completed");
    const updated = {
        ...target,
        outcome,
        outcome_event_ids: params.eventId
            ? [...new Set([...target.outcome_event_ids, params.eventId])]
            : target.outcome_event_ids,
        status: nextStatus,
    };
    const now = params.nowIso ?? new Date().toISOString();
    const entries = store.entries.map((e) => (e.id === updated.id ? updated : e));
    save({ care_key: params.careKey, entries, updated_at: now });
    return updated;
}
function overlap(a, b) {
    const set = new Set(a);
    let n = 0;
    for (const w of b)
        if (set.has(w))
            n += 1;
    return n;
}
/**
 * Answer a record question from decision memory + optional prior observation texts.
 * Never medical advice. Never Clarity form.
 */
function answerRecordQuestion(params) {
    const qTokens = tokens(params.question);
    const entries = listDecisionMemory(params.careKey);
    let best = null;
    let bestScore = 0;
    for (const e of entries) {
        const score = overlap(e.content_tokens, qTokens);
        if (score > bestScore) {
            bestScore = score;
            best = e;
        }
    }
    if (!best || bestScore < 1) {
        for (const raw of params.priorObservationTexts ?? []) {
            if (!(0, decision_signal_2.looksLikeDecisionEvidence)(raw))
                continue;
            const score = overlap(tokens(raw), qTokens);
            if (score > bestScore) {
                bestScore = score;
                best = migrateEntry({
                    id: "ephemeral",
                    care_key: params.careKey,
                    decision: raw.slice(0, 140),
                    reason: null,
                    evidence_texts: [raw.slice(0, 240)],
                    recorded_at: new Date().toISOString(),
                    content_tokens: tokens(raw),
                }, params.careKey);
            }
        }
    }
    if (!best || bestScore < 1) {
        return {
            answered_from_memory: false,
            lines: [],
            evidence_line: null,
            note: "Nothing in the Living Care Record yet explains this — you can add what you know.",
            forces_clarity_form: false,
            reason_unknown: true,
        };
    }
    const lines = [best.what];
    if (best.reason) {
        lines.push(`Reason held: ${best.reason}`);
    }
    else {
        lines.push("Reason for this decision is not held yet.");
    }
    if (best.alternatives.length > 0) {
        lines.push(`Options noted: ${best.alternatives.slice(0, 2).join("; ")}`);
    }
    if (best.outcome) {
        lines.push(`Afterward: ${best.outcome}`);
    }
    const evidenceText = best.evidence[0]?.text ?? best.evidence_texts[0] ?? null;
    return {
        answered_from_memory: true,
        lines: lines.slice(0, 4),
        evidence_line: evidenceText
            ? `From the Living Care Record: ${evidenceText.slice(0, 120)}`
            : null,
        note: "Answered from what is already held — not a Clarity workflow.",
        forces_clarity_form: false,
        reason_unknown: best.reason == null,
    };
}
/**
 * Decision preparation lines for guidance / overload turns —
 * situation understanding, not "what you should choose."
 */
function composeDecisionPreparation(params) {
    const entries = listDecisionMemory(params.careKey);
    const max = params.maxLines ?? 3;
    if (entries.length === 0) {
        return { lines: [], has_decisions: false, open_unknowns: [] };
    }
    const recent = entries.slice(-max);
    const lines = [];
    const open_unknowns = [];
    for (const e of recent) {
        lines.push(e.what);
        if (!e.reason) {
            open_unknowns.push("Why this path was chosen is not held yet.");
        }
        if (e.outcome) {
            lines.push(`Outcome held: ${e.outcome}`);
        }
    }
    return {
        lines: [...new Set(lines)].slice(0, max),
        has_decisions: true,
        open_unknowns: [...new Set(open_unknowns)].slice(0, 2),
    };
}
function resetDecisionMemoryStore() {
    memory.clear();
    (0, fs_store_1.clearDurableDirectory)((0, fs_store_1.livingCareRecordDataDir)("decision-memory"));
}
function clearDecisionMemoryCache() {
    memory.clear();
}
