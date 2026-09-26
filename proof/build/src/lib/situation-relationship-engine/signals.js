"use strict";
/**
 * Shared situation signals for Situation Relationship Engine.
 * Kept free of ACS classify imports to avoid circular deps.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.detectSubjectLabel = detectSubjectLabel;
exports.hasExplicitIdentityConflict = hasExplicitIdentityConflict;
exports.composeIdentityMismatchAsk = composeIdentityMismatchAsk;
exports.isSoftObservationKind = isSoftObservationKind;
exports.isHardEventKind = isHardEventKind;
exports.isEmotionalOrBehavioralText = isEmotionalOrBehavioralText;
exports.sameCalendarDay = sameCalendarDay;
exports.withinActiveWindow = withinActiveWindow;
exports.subjectsCompatible = subjectsCompatible;
exports.referencesHardEventInText = referencesHardEventInText;
exports.continuesUnderlyingIssue = continuesUnderlyingIssue;
exports.looksLikeCareDecision = looksLikeCareDecision;
exports.answersOpenUncertaintyGap = answersOpenUncertaintyGap;
const contract_constants_1 = require("../active-care-situation/contract-constants");
const EMOTIONAL = /\b(frustrat\w*|sad|upset|angry|anxious|scared|lonely|distressed|agitated|mood|crying|tear\w*|want(?:s|ed)? to go home|go home|homesick)\b/i;
/** Topic domains — underlying issue continuity (not keyword note piles). */
const TOPIC_DOMAINS = [
    {
        id: "fall_mobility",
        cue: /\b(fell|fall|fallen|tripped|slipped|walk(?:ing)?|mobility|balance|unsteady|afraid to walk|fear(?:ful)? of walk|limping|hip|knees?)\b/i,
    },
    {
        id: "appetite_fluid",
        cue: /\b(eat|ate|eating|appetite|food|meal|dinner|lunch|breakfast|drink|drinking|fluid|water|refus(?:ed|ing) (?:to )?eat)\b/i,
    },
    {
        id: "mood_behavior",
        cue: /\b(frustrat\w*|sad|upset|angry|anxious|scared|lonely|agitated|mood|crying|confused|confusion|go home|homesick)\b/i,
    },
    {
        id: "medication",
        cue: /\b(medication|medicine|prescription|dose|pill|pills|rx)\b/i,
    },
    {
        id: "sleep_energy",
        cue: /\b(sleep(?:ing)?|tired|nap|fatigue|exhausted|restless)\b/i,
    },
    {
        id: "care_visit",
        cue: /\b(appointment|doctor|hospital|discharg|urgent care|clinic|follow[- ]?up)\b/i,
    },
];
/**
 * Never silently infer Mom/Dad from pronouns or kinship words in notes (identity Locked A).
 * Caregiver-facing labels come from ask-once `resolveSubjectLabel` / durable display_name only.
 * Neutral placeholder keeps subjectsCompatible open until identity is set.
 */
function detectSubjectLabel(text) {
    void text;
    return "they";
}
/**
 * G17 — Protect the Living Care Record: when ACS already has a concrete kinship label,
 * an incoming note that explicitly names a *different* kinship must not attach silently.
 * Does not invent identity; only flags conflict against an already-known label.
 */
function hasExplicitIdentityConflict(activeLabel, rawText) {
    const label = activeLabel.trim();
    if (!label || label === "Your loved one" || label === "they")
        return false;
    const momCue = /\b(mom|mum|mother)\b/i;
    const dadCue = /\b(dad|father|papa|pop)\b/i;
    const activeMom = /^(mom|mum|mother)$/i.test(label);
    const activeDad = /^(dad|father|papa|pop)$/i.test(label);
    const textMom = momCue.test(rawText);
    const textDad = dadCue.test(rawText);
    if (activeMom && textDad && !textMom)
        return true;
    if (activeDad && textMom && !textDad)
        return true;
    return false;
}
/**
 * One soft clarification ask when SRE stamps identity_mismatch (G17).
 * Engine-owned — not a keyword interview template.
 */
function composeIdentityMismatchAsk(activeLabel, rawText) {
    const label = activeLabel.trim();
    const momCue = /\b(mom|mum|mother)\b/i.test(rawText);
    const dadCue = /\b(dad|father|papa|pop)\b/i.test(rawText);
    const incomingKin = dadCue && !momCue ? "Dad" : momCue && !dadCue ? "Mom" : "someone else";
    if (/^(mom|mum|mother)$/i.test(label)) {
        return `This note mentions ${incomingKin} — is this about Mom, or someone else?`;
    }
    if (/^(dad|father|papa|pop)$/i.test(label)) {
        return `This note mentions ${incomingKin} — is this about Dad, or someone else?`;
    }
    if (label && label !== "Your loved one" && label !== "they") {
        return `This note may be about someone other than ${label} — who is it about?`;
    }
    return "This note may be about someone other than who is already held — who is it about?";
}
function isSoftObservationKind(kind) {
    return contract_constants_1.ACTIVE_CARE_SITUATION_SOFT_KINDS.includes(kind);
}
function isHardEventKind(kind) {
    return contract_constants_1.ACTIVE_CARE_SITUATION_HARD_KINDS.includes(kind);
}
function isEmotionalOrBehavioralText(text) {
    return EMOTIONAL.test(text);
}
function sameCalendarDay(aIso, bIso) {
    return aIso.slice(0, 10) === bIso.slice(0, 10);
}
function withinActiveWindow(updatedAt, nowIso) {
    const a = Date.parse(updatedAt);
    const b = Date.parse(nowIso);
    if (!Number.isFinite(a) || !Number.isFinite(b))
        return false;
    return Math.abs(b - a) <= contract_constants_1.ACTIVE_CARE_SITUATION_WINDOW_MS;
}
function subjectsCompatible(activeLabel, incoming) {
    if (incoming === "Your loved one" || incoming === "they")
        return true;
    if (activeLabel === "Your loved one" || activeLabel === "they")
        return true;
    return activeLabel === incoming;
}
function referencesHardEventInText(text) {
    return (/\bfell\b|\bfall\b|\bfallen\b|\btripped\b|\bslipped\b/i.test(text) ||
        /\bhit\s+(her|his|their)\s+head\b|\bhead\s+injur/i.test(text) ||
        /\burgent\s+care\b|\bdischarg/i.test(text) ||
        /\b(medication|medicine|prescription|dose|pill|pills|rx)\b/i.test(text) ||
        /\bappointment\b|\bfollow[- ]?up\b/i.test(text));
}
function domainsInText(text) {
    const found = new Set();
    for (const d of TOPIC_DOMAINS) {
        if (d.cue.test(text))
            found.add(d.id);
    }
    return found;
}
/**
 * Signal 3 — topic / underlying issue (not keyword piles).
 * Fall → afraid to walk shares fall_mobility; appetite thread shares appetite_fluid; etc.
 */
function continuesUnderlyingIssue(active, rawText) {
    const priorText = [
        ...active.observations.map((o) => o.raw_text),
        ...active.observations.map((o) => o.human_fact),
        active.synthesis ?? "",
        active.what_matters_now ?? "",
    ]
        .filter(Boolean)
        .join(" ");
    const priorDomains = domainsInText(priorText);
    if (active.theme === "incident")
        priorDomains.add("fall_mobility");
    if (active.theme === "emotional_behavior")
        priorDomains.add("mood_behavior");
    if (active.theme === "care_change") {
        priorDomains.add("medication");
        priorDomains.add("care_visit");
    }
    if (priorDomains.size === 0)
        return false;
    const incoming = domainsInText(rawText);
    for (const d of incoming) {
        if (priorDomains.has(d))
            return true;
    }
    return false;
}
/** Signal 6 — new care decision should link as related event, not merge into the spine fact. */
const decision_signal_1 = require("../decision-memory/decision-signal");
/** Unified with Decision Memory — sole epistemic decision detector. */
function looksLikeCareDecision(text) {
    return (0, decision_signal_1.looksLikeDecisionEvidence)(text);
}
const STOP = new Set([
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "did",
    "does",
    "do",
    "she",
    "he",
    "they",
    "her",
    "his",
    "their",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "and",
    "or",
    "but",
    "from",
    "this",
    "that",
    "what",
    "when",
    "where",
    "how",
    "why",
    "who",
    "still",
    "open",
    "question",
    "about",
    "any",
    "more",
    "context",
    "would",
    "help",
    "if",
    "you",
    "have",
    "has",
    "had",
]);
function significantTokens(text) {
    return text
        .toLowerCase()
        .replace(/[^\p{L}\p{N}\s]/gu, " ")
        .split(/\s+/)
        .filter((w) => w.length > 2 && !STOP.has(w));
}
/**
 * Signal 4 — answers an open uncertainty on the active situation.
 * Generic overlap / yes-no — not fall→head product quizzes.
 */
function answersOpenUncertaintyGap(active, rawText) {
    const lower = rawText.toLowerCase();
    if (active.open_questions.length === 0)
        return false;
    for (const q of active.open_questions) {
        const ql = q.toLowerCase();
        if (ql.includes("when") && /\b(today|yesterday|this morning|hour|minute|ago|started|began)\b/i.test(lower)) {
            return true;
        }
        if ((ql.includes("before") || ql.includes("usual") || ql.includes("previously")) &&
            /\b(after|before|then|because|first time|usual|usually|always|again|normal)\b/i.test(lower)) {
            return true;
        }
        const looksYn = /^(did|does|do|is|are|was|were|has|have|had|can|could|will|would)\b/.test(ql.trim()) ||
            /\?\s*$/.test(ql);
        if (looksYn &&
            /\b(yes|no|nope|yeah|yep|didn't|did not|never|she did|he did|not sure)\b/i.test(rawText)) {
            return true;
        }
        const qTokens = significantTokens(q);
        const aTokens = new Set(significantTokens(rawText));
        const hits = qTokens.filter((t) => aTokens.has(t)).length;
        if (hits >= 2)
            return true;
        if (qTokens.length <= 3 && hits >= 1 && rawText.trim().length >= 8)
            return true;
    }
    return false;
}
