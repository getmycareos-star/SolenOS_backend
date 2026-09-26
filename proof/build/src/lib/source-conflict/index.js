"use strict";
/**
 * Source Conflict (G12) — document vs note (or opposing sources).
 *
 * Principle: keep both; flag conflict; prefer current fact for orientation
 * without erasing earlier evidence. Never silent winner.
 *
 * Not illustration-keyword product — topic overlap + opposing polarity.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.SOURCE_CONFLICT_PURPOSE = void 0;
exports.recordSourceClaim = recordSourceClaim;
exports.listSourceClaims = listSourceClaims;
exports.resetSourceConflictStore = resetSourceConflictStore;
exports.sourcePriorityRank = sourcePriorityRank;
exports.evaluateSourceConflict = evaluateSourceConflict;
const fs_store_1 = require("../living-care-record-persistence/fs-store");
exports.SOURCE_CONFLICT_PURPOSE = "Hold conflicting sources together — current fact for orientation, nothing erased.";
const claimMemory = new Map();
function claimPath(careKey) {
    return (0, fs_store_1.livingCareRecordDataDir)("source-claims", `${(0, fs_store_1.sanitizeDurableCareKey)(careKey)}.json`);
}
function loadClaims(careKey) {
    const cached = claimMemory.get(careKey);
    if (cached)
        return cached;
    const durable = (0, fs_store_1.readDurableJson)(claimPath(careKey));
    if (durable?.claims) {
        claimMemory.set(careKey, durable);
        return durable;
    }
    return { care_key: careKey, claims: [], updated_at: new Date().toISOString() };
}
function saveClaims(store) {
    claimMemory.set(store.care_key, store);
    (0, fs_store_1.writeDurableJson)(claimPath(store.care_key), store);
}
/** Persist a source claim so conflicts survive ACS opens_new. */
function recordSourceClaim(params) {
    const now = params.capturedAt ?? new Date().toISOString();
    const store = loadClaims(params.careKey);
    const claim = {
        raw_text: params.rawText.trim(),
        kind: params.kind,
        captured_at: now,
    };
    const next = [...store.claims, claim].slice(-40);
    saveClaims({ care_key: params.careKey, claims: next, updated_at: now });
}
function listSourceClaims(careKey) {
    return [...loadClaims(careKey).claims];
}
function resetSourceConflictStore() {
    claimMemory.clear();
    (0, fs_store_1.clearDurableDirectory)((0, fs_store_1.livingCareRecordDataDir)("source-claims"));
}
function isDocumentLike(kind) {
    return (kind === "document" ||
        kind === "hospital_discharge" ||
        kind === "appointment");
}
function isNoteLike(kind) {
    return !isDocumentLike(kind);
}
/**
 * Input Reality source rank — clinical artifacts outrank caregiver memory notes
 * for current-fact orientation. Both sides always retained.
 */
function sourcePriorityRank(kind, rawText = "") {
    const t = rawText.toLowerCase();
    if (kind === "hospital_discharge" ||
        /\b(discharge|discharged|hospital discharge|after.?visit summary|avs)\b/i.test(t)) {
        return 100;
    }
    if (kind === "document" ||
        kind === "appointment" ||
        /\[document:|per (?:the )?records?\b|from the (?:hospital|clinic)\b/i.test(t)) {
        return 80;
    }
    return 20;
}
function preferredOrientation(incoming, prior) {
    const inRank = sourcePriorityRank(incoming.kind, incoming.raw_text);
    const priorRank = sourcePriorityRank(prior.kind, prior.raw_text);
    if (inRank !== priorRank) {
        return inRank > priorRank ? "incoming" : "prior";
    }
    // Same class — newer orients.
    return Date.parse(incoming.captured_at) >= Date.parse(prior.captured_at)
        ? "incoming"
        : "prior";
}
function detectTopic(text) {
    const t = text.toLowerCase();
    if (/\b(eat|ate|eating|appetite|food|meal|dinner|lunch|breakfast|plate)\b/.test(t)) {
        return "eating";
    }
    if (/\b(medication|medicine|pill|dose|rx|prescription|taking)\b/.test(t)) {
        return "medication";
    }
    if (/\b(walk|walking|fell|fall|mobility|stood|standing)\b/.test(t)) {
        return "mobility";
    }
    if (/\b(sleep|sleeping|slept|nap|awake)\b/.test(t)) {
        return "sleep";
    }
    return "other";
}
/** +1 affirming / functioning · -1 declining / refusing · 0 neutral */
function polarity(text, topic) {
    const t = text.toLowerCase();
    if (topic === "eating") {
        if (/\b(ate (?:well|better|normally)|eating (?:well|better|normally)|good appetite|finished)\b/.test(t)) {
            return 1;
        }
        if (/\b(refus|barely ate|not eat|won'?t eat|pushed .{0,20}(?:plate|food)|no appetite)\b/.test(t)) {
            return -1;
        }
    }
    if (topic === "medication") {
        if (/\b(started|prescribed|taking|continues? (?:on|taking)|still (?:on|taking))\b/.test(t)) {
            return 1;
        }
        if (/\b(stopp?ed|not taking|discontinued|held the|missed .{0,20}(?:dose|med))\b/.test(t)) {
            return -1;
        }
    }
    if (topic === "mobility") {
        if (/\b(walked|walking (?:well|better)|steady|stood)\b/.test(t) && !/\b(fell|fall|couldn'?t)\b/.test(t)) {
            return 1;
        }
        if (/\b(fell|fallen|couldn'?t walk|unable to walk|unsteady)\b/.test(t)) {
            return -1;
        }
    }
    if (topic === "sleep") {
        if (/\b(slept (?:well|through)|rested|good night)\b/.test(t))
            return 1;
        if (/\b(not sleep|couldn'?t sleep|up all night|sleeping (?:a lot|all day))\b/.test(t)) {
            return -1;
        }
    }
    return 0;
}
function sourcesDiffer(a, b) {
    const docMark = /\[document:|discharge|from the (?:hospital|clinic)|per (?:the )?records?\b/i;
    return ((isDocumentLike(a.kind) && isNoteLike(b.kind)) ||
        (isNoteLike(a.kind) && isDocumentLike(b.kind)) ||
        docMark.test(a.raw_text) !== docMark.test(b.raw_text));
}
/** Distinct contributors with opposing claims — keep both; never silent winner. */
function multiContributorOppose(a, b) {
    const aId = (a.contributor_id ?? "").trim();
    const bId = (b.contributor_id ?? "").trim();
    return Boolean(aId && bId && aId !== bId);
}
/**
 * Detect opposing document vs note (or opposing sources) on the same topic.
 */
function evaluateSourceConflict(params) {
    const incoming = {
        raw_text: params.incomingText,
        kind: params.incomingKind,
        captured_at: params.incomingCapturedAt ?? new Date().toISOString(),
        contributor_id: params.incomingContributorId ?? null,
    };
    const topic = detectTopic(incoming.raw_text);
    if (topic === "other") {
        return {
            has_conflict: false,
            priority_for_orientation: "neither",
            prior_text: null,
            incoming_text: null,
            note: null,
            pattern_label: null,
            open_ask: null,
            both_retained: true,
        };
    }
    const incomingPol = polarity(incoming.raw_text, topic);
    if (incomingPol === 0) {
        return {
            has_conflict: false,
            priority_for_orientation: "neither",
            prior_text: null,
            incoming_text: null,
            note: null,
            pattern_label: null,
            open_ask: null,
            both_retained: true,
        };
    }
    const priorPool = [
        ...(params.careKey ? listSourceClaims(params.careKey) : []),
        ...(params.priorObservations ?? []),
    ];
    for (const prior of [...priorPool].reverse()) {
        if (prior.raw_text.trim().toLowerCase() === incoming.raw_text.trim().toLowerCase()) {
            continue;
        }
        if (detectTopic(prior.raw_text) !== topic)
            continue;
        const priorPol = polarity(prior.raw_text, topic);
        if (priorPol === 0 || priorPol === incomingPol)
            continue;
        const multi = multiContributorOppose(prior, incoming);
        if (!sourcesDiffer(prior, incoming) && !multi)
            continue;
        const priority_for_orientation = preferredOrientation(incoming, prior);
        const clinicalPreferred = Math.max(sourcePriorityRank(incoming.kind, incoming.raw_text), sourcePriorityRank(prior.kind, prior.raw_text)) >= 80;
        return {
            has_conflict: true,
            priority_for_orientation,
            prior_text: prior.raw_text,
            incoming_text: incoming.raw_text,
            note: multi
                ? "More than one contributor reported differently — both are held. Nothing was erased."
                : clinicalPreferred
                    ? "Sources do not fully agree — both are held. Current orientation prefers the clinical or document source; nothing was erased."
                    : "Sources do not fully agree — both are held. Nothing was erased.",
            pattern_label: multi ? "disagreeing care views" : "source conflict",
            open_ask: multi
                ? "Both views stay in the record — what are you seeing now?"
                : "Which of these matches what you are seeing now?",
            both_retained: true,
        };
    }
    return {
        has_conflict: false,
        priority_for_orientation: "neither",
        prior_text: null,
        incoming_text: null,
        note: null,
        pattern_label: null,
        open_ask: null,
        both_retained: true,
    };
}
