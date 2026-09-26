"use strict";
/**
 * Care memory maturity — first vs returning Care Reality orientation.
 * SoT: docs/02-product/solenos-first-vs-returning-user.md
 * Confirmation gate: docs/02-product/solenos-response-contract.md (Relief disclosure decision)
 * Examples in docs are illustrations only — never product templates.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.FAKE_CONTINUITY_PHRASES = exports.CARE_STORY_CHROME_PATTERNS = exports.CARE_MEMORY_MATURITY_PURPOSE = void 0;
exports.containsCareStoryChrome = containsCareStoryChrome;
exports.containsFakeContinuity = containsFakeContinuity;
exports.classifyCareMemoryState = classifyCareMemoryState;
exports.isNewCareReality = isNewCareReality;
exports.composeNewCareRealityConfirmation = composeNewCareRealityConfirmation;
exports.composeReturningCareRealityConfirmation = composeReturningCareRealityConfirmation;
exports.composeAwaitingCareEvidenceConfirmation = composeAwaitingCareEvidenceConfirmation;
exports.composeAwaitingCareEvidenceRecognition = composeAwaitingCareEvidenceRecognition;
exports.composeProductMetaTurnRecognition = composeProductMetaTurnRecognition;
exports.composeProductMetaTurnConfirmation = composeProductMetaTurnConfirmation;
exports.composeIdentityMismatchConfirmation = composeIdentityMismatchConfirmation;
exports.composeIdentityMismatchRecognition = composeIdentityMismatchRecognition;
exports.resolveCareTurnConfirmation = resolveCareTurnConfirmation;
exports.caregiverNoteMetaLabel = caregiverNoteMetaLabel;
exports.composeMemoryAwareSoftSummary = composeMemoryAwareSoftSummary;
exports.composeMemoryAwareWhatChanged = composeMemoryAwareWhatChanged;
exports.composeReturningOrientationLines = composeReturningOrientationLines;
const care_epistemics_1 = require("../care-epistemics");
exports.CARE_MEMORY_MATURITY_PURPOSE = "Orient as beginning a care story (new) or continuing one (returning) — never fake continuity, never restart.";
/** Caregiver-facing storage/continuity chrome — forbidden until careWorthyCount ≥ 1 and turn is care-worthy. */
exports.CARE_STORY_CHROME_PATTERNS = [
    /\badded to (?:the|[\w']+'s) care story\b/i,
    /\bcare story already underway\b/i,
    /\bbeginning of (?:the|[\w']+'s) living care record\b/i,
    /\badded to (?:the|[\w']+'s) living care record\b/i,
    /\bupdated (?:the|[\w']+'s) living care record\b/i,
    /\bthis becomes part of (?:the|[\w']+'s) ongoing care story\b/i,
    /\bconnected to what is already held\b/i,
];
function containsCareStoryChrome(text) {
    return exports.CARE_STORY_CHROME_PATTERNS.some((p) => p.test(text));
}
/** Phrases that invent prior relationship when none exists. */
exports.FAKE_CONTINUITY_PHRASES = [
    "stays connected to what is already held",
    "stays connected to what was already held",
    "connected to what you already shared",
    "as we previously",
    "as we already discussed",
    "picking up where we left off",
    "from our last conversation",
];
function containsFakeContinuity(text) {
    const lower = text.toLowerCase();
    return exports.FAKE_CONTINUITY_PHRASES.some((p) => lower.includes(p));
}
/**
 * Classify from held care-memory depth — not chat session flags.
 * Returning once there is prior *care* observation history (or CRS revisions).
 * Product/meta session notes do not count as care memory.
 */
function classifyCareMemoryState(params) {
    const careWorthy = params.careWorthyObservationCount != null
        ? params.careWorthyObservationCount
        : Math.max(params.observationCount, params.crsObservationCount ?? 0);
    const revision = params.crsRevision ?? 0;
    // First care-worthy capture (or only meta so far) = beginning the care story.
    if (careWorthy <= 1)
        return "new_care_reality";
    if (revision <= 1 && careWorthy <= 1)
        return "new_care_reality";
    return "returning_care_reality";
}
function isNewCareReality(params) {
    return classifyCareMemoryState(params) === "new_care_reality";
}
/**
 * Confirmation for first capture — beginning of understanding, not storage theater.
 */
function composeNewCareRealityConfirmation(params) {
    const who = params.subjectLabel?.trim();
    if (who && who !== "they" && who !== "Your loved one" && who !== "person") {
        return `Beginning of ${who}'s Living Care Record — held so you do not have to reconstruct it later.`;
    }
    return "Beginning of the Living Care Record — held so you do not have to reconstruct it later.";
}
/**
 * Confirmation when care memory already exists.
 */
function composeReturningCareRealityConfirmation(params) {
    const who = params.subjectLabel?.trim();
    if (who && who !== "they" && who !== "Your loved one" && who !== "person") {
        return `Updated ${who}'s Living Care Record — connected to what is already held.`;
    }
    return "Updated the Living Care Record — connected to what is already held.";
}
/** No care-worthy evidence in the record yet — never "Added to the care story". */
function composeAwaitingCareEvidenceConfirmation(params) {
    const who = params.subjectLabel?.trim();
    if (who && who !== "they" && who !== "Your loved one" && who !== "person") {
        return `Nothing about ${who}'s care is held yet. Share what is happening — notes, documents, or photos are all fine.`;
    }
    return "Nothing about the person's care is held yet. Share what is happening — notes, documents, or photos are all fine.";
}
/** Recognition when the record has no care anchors yet. */
function composeAwaitingCareEvidenceRecognition() {
    return "Share what is happening with care when you are ready — fragments are fine.";
}
/** Recognition when this turn is product meta but prior care evidence exists. */
function composeProductMetaTurnRecognition() {
    return "That note was about using SolenOS — not the person's care. What you already shared about care stays held.";
}
/** Latest turn is product/session meta — not a care observation. */
function composeProductMetaTurnConfirmation(params) {
    if (params.hasPriorCareEvidence) {
        return "That note was about using SolenOS — not about the person's care. What you already shared about care stays held.";
    }
    return composeAwaitingCareEvidenceConfirmation({ subjectLabel: params.subjectLabel });
}
/** G17 — hold mismatched note; one soft ask; never care-story theater. */
function composeIdentityMismatchConfirmation(params) {
    const who = params.activeSubjectLabel?.trim();
    const named = who && who !== "Your loved one" && who !== "they" && who !== "person" ? who : null;
    return named
        ? `This note is held for now — it will not be linked to ${named}'s record until it is clear who it is about.`
        : "This note is held for now — it will not be linked to the care record until it is clear who it is about.";
}
function composeIdentityMismatchRecognition(params) {
    const who = params.activeSubjectLabel?.trim();
    const named = who && who !== "Your loved one" && who !== "they" && who !== "person" ? who : null;
    return named
        ? `What is already held about ${named} stays as-is — this new note may be about someone else.`
        : "What is already held stays as-is — this new note may be about someone else.";
}
/**
 * Single confirmation gate — care-story / LCR chrome only when careWorthyCount ≥ 1
 * and the current turn is care-worthy. Product meta never earns "Added to the care story".
 */
function resolveCareTurnConfirmation(params) {
    const who = params.subjectLabel?.trim();
    const named = who && who !== "Your loved one" && who !== "they" && who !== "person" ? who : null;
    if (params.turnClass === "pushback") {
        return "Understood — that question will not be asked again.";
    }
    if (params.turnClass === "identity_mismatch") {
        return composeIdentityMismatchConfirmation({ activeSubjectLabel: params.subjectLabel });
    }
    if (params.turnClass === "empty_or_thin") {
        return "Nothing new was added.";
    }
    if (params.turnClass === "record_question") {
        return named
            ? `Using what is already held in ${named}'s Living Care Record.`
            : "Using what is already held in the Living Care Record.";
    }
    // Locked: no care anchors yet — invite only; never care-story / LCR theater.
    if (params.careWorthyCount <= 0 || !params.hasCareEvidence) {
        return composeAwaitingCareEvidenceConfirmation({ subjectLabel: named });
    }
    // Locked: product/session meta turn — prior care may exist; this turn does not add to the care story.
    if (!params.latestIsCareWorthy &&
        (0, care_epistemics_1.isProductSessionMetaText)(params.latestRawText)) {
        return composeProductMetaTurnConfirmation({
            hasPriorCareEvidence: true,
            subjectLabel: named,
        });
    }
    if (params.improvement) {
        return named
            ? `Updated what we understand about ${named} — the latest change is the current picture.`
            : "Updated what we understand — the latest change is the current picture.";
    }
    // Thin follow-up without a new care anchor — do not claim LCR / care-story update.
    if (!params.latestIsCareWorthy &&
        !(0, care_epistemics_1.isProductSessionMetaText)(params.latestRawText) &&
        !["continuity_symptom", "improvement", "answer_to_open"].includes(params.turnClass)) {
        if (params.gatheringContext) {
            return named
                ? `This is held in ${named}'s Living Care Record. A couple of details would help before deciding what matters.`
                : "This is held. A couple of details would help before deciding what matters.";
        }
        return named
            ? `What we understand about ${named}'s care continues — share what changed when you can.`
            : "What we already understand continues — share what changed when you can.";
    }
    // careWorthyCount ≥ 1 and current turn is care-worthy — care continuity confirmations allowed.
    if (params.turnClass === "emotional_only") {
        return params.isNewCareReality
            ? composeNewCareRealityConfirmation({ subjectLabel: named })
            : composeReturningCareRealityConfirmation({ subjectLabel: named });
    }
    if (params.turnClass === "document" || (params.hasDocuments && params.kind === "document")) {
        return params.isNewCareReality
            ? composeNewCareRealityConfirmation({ subjectLabel: named })
            : composeReturningCareRealityConfirmation({ subjectLabel: named });
    }
    if (params.continuitySymptom &&
        params.priorObservationFactsCount > 0 &&
        !params.gatheringContext) {
        return "Here is what is already held — not a recommendation for what to choose.";
    }
    if (params.gatheringContext) {
        return named
            ? `This is held in ${named}'s Living Care Record. A couple of details would help before deciding what matters.`
            : "This is held. A couple of details would help before deciding what matters.";
    }
    if (params.isNewCareReality ||
        params.relation === "opens_new" ||
        params.careWorthyCount <= 1) {
        return composeNewCareRealityConfirmation({ subjectLabel: named });
    }
    if (params.patternLabel === "day-to-day fluctuation") {
        return named
            ? `Updated ${named}'s record — today can look different from recent days.`
            : "Updated — today can look different from recent days.";
    }
    if (params.patternLabel === "gradual daily-living changes") {
        return named
            ? `Updated ${named}'s record — small changes over time are being held together.`
            : "Updated — small changes over time are being held together.";
    }
    return composeReturningCareRealityConfirmation({ subjectLabel: named });
}
/** Caregiver note chip — never "Added to the care story" until careWorthyCount ≥ 1 and latest is care-worthy. */
function caregiverNoteMetaLabel(params) {
    if (params.careWorthyCount <= 0) {
        return "Waiting for care to share";
    }
    if (params.latestIsCareWorthy) {
        return "Added to the care record";
    }
    return "About SolenOS — what you shared stays in the care record";
}
/**
 * Soft orientation when understanding is thin — state-aware.
 */
function composeMemoryAwareSoftSummary(params) {
    if (params.state === "new_care_reality") {
        return "Beginning to understand this care situation — saved so you do not have to reconstruct it alone later.";
    }
    return "This stays connected to what is already noted — clarity grows as more of the care record builds.";
}
/**
 * What changed / first-anchor line — never invent prior comparison for new users.
 */
function composeMemoryAwareWhatChanged(params) {
    if (params.baselineChangeNote?.trim()) {
        return params.baselineChangeNote.trim();
    }
    if (params.state === "new_care_reality") {
        return "First care observations are held — unknowns stay open until clearer evidence arrives.";
    }
    const prior = params.priorFact?.trim();
    const latest = params.latestFact?.trim();
    if (prior && latest && prior.toLowerCase() !== latest.toLowerCase()) {
        return `New: ${latest.replace(/\.$/, "")}. Earlier held: ${prior.replace(/\.$/, "")}.`;
    }
    return "This stays connected to what was already held — you do not have to reconstruct the thread.";
}
/**
 * Returning orientation: already known vs new (caregiver-facing lines).
 */
function composeReturningOrientationLines(params) {
    const max = params.max ?? 2;
    const prior = params.priorFacts.map((f) => f.trim()).filter(Boolean);
    const latest = params.latestFacts.map((f) => f.trim()).filter(Boolean);
    const priorLower = new Set(prior.map((p) => p.toLowerCase()));
    const what_is_new = latest
        .filter((l) => !priorLower.has(l.toLowerCase()))
        .slice(0, max);
    const already_known = prior.slice(0, max);
    return { already_known, what_is_new };
}
