"use strict";
/**
 * Care Recipient Anchor — center of gravity for every interaction.
 * Processing order: recipient → changes → events → decisions → outcomes → unknowns → contributor context.
 *
 * SoT: docs/02-product/solenos-care-recipient-anchor.md
 * Identity naming Locked A: never silently write Mom/Dad into durable identity from notes.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.CARE_REALITY_PROCESSING_ORDER = exports.CARE_RECIPIENT_ANCHOR_PURPOSE = void 0;
exports.composeCareRecipientIdentityAsk = composeCareRecipientIdentityAsk;
exports.detectSessionKinshipCue = detectSessionKinshipCue;
exports.composeSessionKinshipConfirmAsk = composeSessionKinshipConfirmAsk;
exports.buildCareRecipientAnchor = buildCareRecipientAnchor;
exports.orientationFromCareRecipientAnchor = orientationFromCareRecipientAnchor;
exports.centersContributorConflictOverRecipient = centersContributorConflictOverRecipient;
const care_recipient_identity_1 = require("../care-recipient-identity");
const care_reality_extraction_1 = require("../care-reality-extraction");
const classify_1 = require("../care-reality-extraction/classify");
const care_epistemics_1 = require("../care-epistemics");
const multi_caregiver_context_model_1 = require("../multi-caregiver-context-model");
exports.CARE_RECIPIENT_ANCHOR_PURPOSE = "Anchor every interaction on the person receiving care — contributors are context, never the subject.";
/** Non-negotiable processing order after the anchor is resolved. */
exports.CARE_REALITY_PROCESSING_ORDER = [
    "care_recipient",
    "current_state_changes",
    "care_events",
    "care_decisions",
    "outcomes",
    "unknowns",
    "caregiver_context",
];
const GENERIC_SUBJECTS = new Set([
    "they",
    "your loved one",
    "the patient",
    "the subject",
    "person",
    "",
]);
function isKnownRecipientLabel(label) {
    if (!label)
        return false;
    return !GENERIC_SUBJECTS.has(label.trim().toLowerCase());
}
/**
 * Soft identity ask — Locked A natural language; never a form wall.
 */
function composeCareRecipientIdentityAsk() {
    return "Who is this situation about?";
}
/**
 * Session-only kinship cue from capture text.
 * Never writes durable identity (Locked A) — orientation only so we do not ask
 * "Who is this situation about?" when the note already names Mom/Dad/etc.
 */
function detectSessionKinshipCue(text) {
    const t = text.trim();
    if (!t)
        return null;
    if (/\b(?:my\s+)?(?:mom|mum|mother)\b/i.test(t))
        return "Mom";
    if (/\b(?:my\s+)?(?:dad|father)\b/i.test(t))
        return "Dad";
    if (/\b(?:my\s+)?(?:grandma|grandmother)\b/i.test(t))
        return "Grandma";
    if (/\b(?:my\s+)?(?:grandpa|grandfather)\b/i.test(t))
        return "Grandpa";
    return null;
}
/**
 * Soft confirm when kinship is present but durable name has not been asked yet.
 * Prefer this over blank "Who is this?" when the note already anchors the person.
 */
function composeSessionKinshipConfirmAsk(kinshipLabel) {
    return `Is ${kinshipLabel} the name you use for the person this care story is about?`;
}
/**
 * Build Care Recipient Anchor before situation modeling or response language.
 * Does not write durable identity from note kinship terms (Locked A).
 */
function buildCareRecipientAnchor(params) {
    const { situation } = params;
    const rawKey = params.careKey ?? situation.care_recipient_id ?? situation.caregiver_id;
    const careKey = (0, multi_caregiver_context_model_1.resolveCareRealityStoreKey)(rawKey);
    // Lookup by minted Care Reality id and raw contributor id (identity may be set either way).
    const durable = (0, care_recipient_identity_1.getCareRecipientDisplayName)(careKey) ??
        (rawKey !== careKey ? (0, care_recipient_identity_1.getCareRecipientDisplayName)(rawKey) : null) ??
        (situation.caregiver_id &&
            situation.caregiver_id !== careKey &&
            situation.caregiver_id !== rawKey
            ? (0, care_recipient_identity_1.getCareRecipientDisplayName)(situation.caregiver_id)
            : null);
    const fromAcs = situation.subject_label?.trim() ?? null;
    const latest = params.latestRawText?.trim() ?? "";
    const sessionKinship = detectSessionKinshipCue(latest);
    const care_recipient = isKnownRecipientLabel(durable)
        ? durable
        : isKnownRecipientLabel(fromAcs)
            ? fromAcs
            : sessionKinship;
    // Blank-slate ask only when no durable, ACS, or session kinship cue.
    const needs_identity_ask = !care_recipient;
    const identity_ask = needs_identity_ask
        ? composeCareRecipientIdentityAsk()
        : !isKnownRecipientLabel(durable) && sessionKinship
            ? composeSessionKinshipConfirmAsk(sessionKinship)
            : composeCareRecipientIdentityAsk();
    const extraction = latest.length >= 40
        ? (0, care_reality_extraction_1.extractCareRealityFromText)({
            rawText: latest,
            contributorId: situation.caregiver_id,
        })
        : null;
    const recipient_changes = [];
    const related_events = [];
    const related_decisions = [];
    const unknowns = [];
    const contributor_context = [];
    if (extraction) {
        for (const o of extraction.observations) {
            recipient_changes.push(o.description.endsWith(".") ? o.description : `${o.description}.`);
        }
        for (const e of extraction.events) {
            related_events.push(e.description);
        }
        for (const d of extraction.decisions) {
            related_decisions.push(d.description);
        }
        for (const u of extraction.unknowns) {
            if (u.status === "open")
                unknowns.push(u.question);
        }
        for (const n of extraction.non_care_facts) {
            contributor_context.push(n.text);
        }
    }
    // Held ACS observations — recipient-centered only
    for (const o of [...situation.observations].reverse()) {
        const fact = (0, care_epistemics_1.observationCareFact)({
            human_fact: o.human_fact,
            raw_text: o.raw_text,
        });
        if (!fact)
            continue;
        const cat = (0, classify_1.classifyExtractionFragment)(fact);
        if (cat === "contributor_load" || cat === "disagreement_perspective") {
            if (!contributor_context.some((c) => c.includes(fact.slice(0, 40)))) {
                contributor_context.push(fact);
            }
            continue;
        }
        if (cat === "observation" || cat === "event" || cat === "decision") {
            const line = fact.endsWith(".") ? fact : `${fact}.`;
            if (cat === "observation" &&
                !recipient_changes.some((x) => x.toLowerCase().includes(fact.toLowerCase().slice(0, 36)))) {
                recipient_changes.push(line);
            }
        }
        if (recipient_changes.length >= 6)
            break;
    }
    return {
        care_recipient,
        needs_identity_ask,
        identity_ask,
        contributor_id: situation.caregiver_id,
        recipient_changes: recipient_changes.slice(0, 6),
        related_events: related_events.slice(0, 4),
        related_decisions: related_decisions.slice(0, 4),
        unknowns: unknowns.slice(0, 4),
        contributor_context: contributor_context.slice(0, 4),
        extraction,
        anchored: Boolean(care_recipient) || recipient_changes.length > 0 || related_events.length > 0,
    };
}
/**
 * Orientation centered on the care recipient — never takes sides in family disagreement.
 */
function orientationFromCareRecipientAnchor(anchor) {
    if (anchor.needs_identity_ask) {
        return {
            current_understanding: null,
            related_context: null,
            still_unclear: [],
            identity_ask: anchor.identity_ask,
        };
    }
    const who = anchor.care_recipient;
    let current_understanding = null;
    if (anchor.recipient_changes.length > 0) {
        const bits = anchor.recipient_changes.slice(0, 2).map((c) => c.replace(/\.$/, ""));
        current_understanding = `Recent changes in ${who}'s care reality are held — ${bits.join("; ")}.`;
    }
    else if (anchor.related_events.length > 0) {
        current_understanding = `A care journey moment for ${who} is held: ${anchor.related_events[0].replace(/\.$/, "")}.`;
    }
    else if (anchor.related_decisions.length > 0) {
        current_understanding = `A care choice about ${who} is held: ${anchor.related_decisions[0].replace(/\.$/, "")}.`;
    }
    let related_context = null;
    if (anchor.contributor_context.length > 0) {
        related_context =
            "Different people may see the situation differently depending on how often they are present — held as context, not as the main concern.";
    }
    return {
        current_understanding,
        related_context,
        still_unclear: anchor.unknowns.slice(0, 2),
        identity_ask: null,
    };
}
/**
 * True when caregiver-facing text wrongly centers family disagreement over the recipient.
 */
function centersContributorConflictOverRecipient(params) {
    if (!params.hasRecipientChanges)
        return false;
    const b = params.blob.toLowerCase();
    // Explicitly holding family as context is correct — not centering conflict
    if (/\b(?:held as context|not as the main (?:care )?situation|not as the main concern)\b/i.test(b) &&
        !/\bthe (?:main|biggest) (?:issue|problem|concern) is (?:your )?(?:brother|sister|family)\b/i.test(b)) {
        return false;
    }
    const conflictFocus = /\b(?:brother|sister|sibling)\b/i.test(b) &&
        /\b(?:worrying too much|overreact|doesn't understand|not understand|disagreement)\b/i.test(b);
    if (!conflictFocus)
        return false;
    const who = params.careRecipient?.toLowerCase();
    if (who && b.includes(who.toLowerCase())) {
        // Mentions recipient AND conflict — OK if recipient appears first or as subject
        const recipIdx = b.indexOf(who);
        const brotherIdx = b.search(/\bbrother|sister|sibling\b/);
        if (recipIdx >= 0 && (brotherIdx < 0 || recipIdx < brotherIdx))
            return false;
    }
    return true;
}
