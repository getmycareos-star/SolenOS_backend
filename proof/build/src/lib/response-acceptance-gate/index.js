"use strict";
/**
 * Response Acceptance Gate — reject transformation failures before caregivers see output.
 * SoT: docs/02-product/solenos-response-intelligence-upgrade.md
 * NOT UI copy — enforces Care Reality Model → caregiver response contract.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.RESPONSE_DIAGNOSIS_PATTERNS = exports.RESPONSE_EMPATHY_FAILURE_PATTERNS = exports.RESPONSE_TASKIFY_PATTERNS = exports.RESPONSE_SUMMARY_FAILURE_PATTERNS = exports.RESPONSE_NOTES_DOCUMENTATION_PATTERNS = exports.RESPONSE_ORIENTATION_ANTIPATTERN = exports.RESPONSE_ACCEPTANCE_SECTIONS = exports.RESPONSE_ACCEPTANCE_PURPOSE = void 0;
exports.assertResponseAcceptanceGate = assertResponseAcceptanceGate;
exports.evaluateResponseAcceptance = evaluateResponseAcceptance;
const care_memory_maturity_1 = require("../care-memory-maturity");
const output_quality_1 = require("../output-quality");
const care_epistemics_1 = require("../care-epistemics");
const no_hardcode_contract_1 = require("../care-reality-intelligence/no-hardcode-contract");
const caregiver_understanding_output_1 = require("../caregiver-understanding-output");
exports.RESPONSE_ACCEPTANCE_PURPOSE = "Reject caregiver output that summarizes without understanding — enforce Events → Relationships → Understanding → Confidence.";
/** Required transformation sections (disclosure may hide; layer must produce when evidence allows). */
exports.RESPONSE_ACCEPTANCE_SECTIONS = [
    "recognition",
    "current_understanding",
    "connections",
    "what_matters_now",
    "what_remains_unclear",
    "care_story_update",
];
/** Orientation / checklist / notes-app anti-patterns — reject (Care Reality Situation Model SoT). */
exports.RESPONSE_ORIENTATION_ANTIPATTERN = [
    /\bhere are your tasks\b/i,
    /\bhere are your notes\b/i,
    /\bhere are \d+ things to monitor\b/i,
    /\byou should contact\b/i,
    /\byour care summary\b/i,
    /\bthe most important sentence was\b/i,
    /\bthings to monitor\b/i,
    /\bcare checklist\b/i,
];
/** Notes-app / documentation theater — reject (Care Reality Language SoT). */
exports.RESPONSE_NOTES_DOCUMENTATION_PATTERNS = [
    /\bi (?:have )?added this to your care notes\b/i,
    /\byour notes (?:show|say|indicate)\b/i,
    /\bbased on your previous entries\b/i,
    /\bi saved (?:a |this )?note\b/i,
    /\bsaved (?:a |this )?note about\b/i,
    /\badded to your care notes\b/i,
    /\bcare notes (?:stay|are|show)\b/i,
    /\brelated notes?\b/i,
    /\btoday'?s notes\b/i,
    /\bheld with today'?s notes\b/i,
    /\ba related note was added\b/i,
    /\bstored notes\b/i,
    /\bnote history\b/i,
    /\bsupporting notes\b/i,
    /\bmore complete records?\b/i,
    /\bevidence maturity\b/i,
    /\blower attention items?\b/i,
];
/** Patterns that signal ChatGPT-style / document summarization — reject. */
exports.RESPONSE_SUMMARY_FAILURE_PATTERNS = [
    /here is what i understand/i,
    /here'?s what i understand/i,
    /here'?s my understanding/i,
    /what i understand (?:from|is|so far)/i,
    /my understanding (?:is|of this)/i,
    /putting this together/i,
    /i'?m hearing that/i,
    /concern identified/i,
    /stress reported/i,
    /possible issue/i,
    /based on your message/i,
    /based on what you (?:wrote|shared|said)/i,
    /based on today'?s note/i,
    /looking at what you wrote/i,
    /i'?ve summarized/i,
    /key points from your/i,
    /key takeaways/i,
    /here'?s the gist/i,
    /\bin short[,:]/i,
    /the ai analyzed/i,
    /your message (?:shows|indicates|suggests)/i,
    /here is (?:a |your )?summary of (?:your )?(?:document|pdf|file|upload)/i,
    /here'?s (?:a |your )?summary of/i,
    /\bdocument summary\b/i,
    /\bi (?:have )?extracted (?:the )?(?:following|key)/i,
    /\bin summary[,:]/i,
    /\bto summarize[,:]/i,
];
/** Task-manager chrome in what matters now — reject. */
exports.RESPONSE_TASKIFY_PATTERNS = [
    /\btodo\b/i,
    /\btask list\b/i,
    /\bchecklist\b/i,
    /\bstep 1\b/i,
    /\bstep 2\b/i,
    /\baction items?:/i,
    /\bnext actions?:/i,
    /\bthings to keep an eye on\b/i,
];
/** Generic empathy / companionship — reject. */
exports.RESPONSE_EMPATHY_FAILURE_PATTERNS = [
    /i understand how you feel/i,
    /i'?m here for you/i,
    /i'?m sorry you(?:'re| are) going through/i,
    /you are doing (?:great|amazing)/i,
    /thanks for sharing/i,
    /that sounds difficult/i,
    /it sounds like you(?:'re| are)/i,
    /your feelings are valid/i,
];
/** Medical conclusion theater — reject. */
exports.RESPONSE_DIAGNOSIS_PATTERNS = [
    /\b(?:likely|probably|appears to have)\s+(?:dementia|alzheimer|depression)\b/i,
    /this (?:is|means) (?:dementia|depression|progression)/i,
    /\bthis looks like (?:dementia )?progression\b/i,
    /\bconsistent with (?:dementia|alzheimer) (?:decline|progression)\b/i,
    /\bdiagnosed with\b/i,
];
function responseBlob(composed) {
    return [
        composed.recognition_line ?? "",
        composed.confirmation,
        composed.situation_summary ?? "",
        ...(composed.what_we_know ?? []),
        composed.connection_note ?? "",
        composed.what_changed ?? "",
        composed.what_matters_now ?? "",
        composed.what_can_wait ?? "",
        composed.what_may_become_serious ?? "",
        ...(composed.still_unclear ?? []),
        composed.care_story_update ?? "",
        composed.evidence_line ?? "",
        ...(composed.follow_up_items ?? []),
    ]
        .filter(Boolean)
        .join("\n");
}
function matchesAny(text, patterns) {
    return patterns.some((p) => p.test(text));
}
/** Task-manager chrome — allow explicit anti-task phrasing ("not a task list"). */
function looksTaskifiedWhatMattersNow(text) {
    const t = text.trim();
    if (!t)
        return false;
    if (/\b(not|never|without|avoid).{0,40}\btask list\b/i.test(t))
        return false;
    if (/\bnot turning it into a task list\b/i.test(t))
        return false;
    return matchesAny(t, exports.RESPONSE_TASKIFY_PATTERNS);
}
/**
 * Strict gate — throw on failure conditions. Call after compose, before render.
 */
function assertResponseAcceptanceGate(params) {
    const { composed, careMemoryState, observationCount, turnClass } = params;
    const blob = responseBlob(composed);
    const lower = blob.toLowerCase();
    if (matchesAny(blob, exports.RESPONSE_SUMMARY_FAILURE_PATTERNS)) {
        throw new Error("Response acceptance: ChatGPT-style summarization rejected");
    }
    if ((0, caregiver_understanding_output_1.containsDocumentSummarizerTheater)(blob) || (0, caregiver_understanding_output_1.containsEchoSummaryTheater)(blob)) {
        throw new Error("Response acceptance: document/echo summarizer theater rejected");
    }
    if (matchesAny(blob, exports.RESPONSE_NOTES_DOCUMENTATION_PATTERNS)) {
        throw new Error("Response acceptance: notes/documentation language rejected");
    }
    if (matchesAny(blob, exports.RESPONSE_ORIENTATION_ANTIPATTERN)) {
        throw new Error("Response acceptance: notes/checklist/advice orientation anti-pattern rejected");
    }
    if ((0, no_hardcode_contract_1.containsKeywordClassifierTheater)(blob)) {
        throw new Error("Response acceptance: keyword-classifier theater rejected");
    }
    if (matchesAny(blob, exports.RESPONSE_EMPATHY_FAILURE_PATTERNS)) {
        throw new Error("Response acceptance: generic empathy script rejected");
    }
    if (looksTaskifiedWhatMattersNow(composed.what_matters_now ?? "")) {
        throw new Error("Response acceptance: task-list what-matters-now rejected");
    }
    if (matchesAny(blob, exports.RESPONSE_DIAGNOSIS_PATTERNS)) {
        throw new Error("Response acceptance: medical conclusion rejected");
    }
    if ((0, output_quality_1.containsInternalLanguage)(blob)) {
        throw new Error("Response acceptance: internal architecture language rejected");
    }
    const skipStructure = turnClass === "empty_or_thin" ||
        turnClass === "pushback" ||
        turnClass === "record_question" ||
        turnClass === "identity_mismatch";
    const awaitingCare = params.careWorthyCount <= 0 || !params.latestIsCareWorthy;
    if (turnClass === "identity_mismatch") {
        if ((composed.still_unclear?.length ?? 0) > 1) {
            throw new Error("Response acceptance: identity mismatch allows at most one ask");
        }
        if ((composed.still_unclear?.length ?? 0) === 0) {
            throw new Error("Response acceptance: identity mismatch requires one clarification ask");
        }
        if ((0, care_memory_maturity_1.containsCareStoryChrome)(blob)) {
            throw new Error("Response acceptance: care-story chrome on identity mismatch rejected");
        }
        if (composed.care_story_update?.trim()) {
            throw new Error("Response acceptance: care story update on identity mismatch rejected");
        }
        if (composed.connection_note?.trim()) {
            throw new Error("Response acceptance: fake connection on identity mismatch rejected");
        }
        if (!composed.recognition_line?.trim()) {
            throw new Error("Response acceptance: identity mismatch missing recognition line");
        }
        return;
    }
    if (!skipStructure) {
        // Rule 3: recognition before organization (invite counts when awaiting care evidence)
        if (!composed.recognition_line?.trim()) {
            throw new Error("Response acceptance: missing recognition line");
        }
        // Care story update — only when this turn added care-worthy evidence
        if (!awaitingCare) {
            if (!composed.care_story_update?.trim()) {
                throw new Error("Response acceptance: missing care story update");
            }
        }
        else if (composed.care_story_update?.trim()) {
            throw new Error("Response acceptance: care story update before care-worthy evidence rejected");
        }
        if (params.careWorthyCount <= 0) {
            if ((0, care_memory_maturity_1.containsCareStoryChrome)(blob)) {
                throw new Error("Response acceptance: care-story chrome before care evidence rejected");
            }
        }
        else if (!params.latestIsCareWorthy &&
            (0, care_epistemics_1.isProductSessionMetaText)(params.latestRawText.trim())) {
            const turnChrome = `${composed.confirmation}\n${composed.care_story_update ?? ""}`;
            if ((0, care_memory_maturity_1.containsCareStoryChrome)(turnChrome)) {
                throw new Error("Response acceptance: care-story chrome on product meta turn rejected");
            }
        }
        else if (!params.latestIsCareWorthy &&
            (0, care_memory_maturity_1.containsCareStoryChrome)(`${composed.confirmation}\n${composed.care_story_update ?? ""}`)) {
            throw new Error("Response acceptance: care-story chrome on non-care-worthy turn rejected");
        }
        // Current understanding — recognition alone is not enough for care-worthy turns.
        const hasUnderstanding = (composed.what_we_know?.length ?? 0) > 0 ||
            Boolean(composed.situation_summary?.trim()) ||
            Boolean(composed.what_changed?.trim()) ||
            turnClass === "emotional_only" ||
            (!composed.show_clarity &&
                !params.latestIsCareWorthy &&
                Boolean(composed.recognition_line?.trim()));
        if (!hasUnderstanding && turnClass !== "document") {
            throw new Error("Response acceptance: missing current understanding");
        }
    }
    // Orientable Clarity (Response Contract relief): require orientation fields, never ask-echo as matters.
    if (composed.show_clarity && !skipStructure) {
        if (!composed.what_matters_now?.trim()) {
            throw new Error("Response acceptance: Clarity without what_matters_now rejected");
        }
        if (!composed.what_can_wait?.trim()) {
            throw new Error("Response acceptance: Clarity without what_can_wait rejected");
        }
        const hasHappening = Boolean(composed.situation_summary?.trim()) ||
            (composed.what_we_know?.length ?? 0) > 0;
        if (!hasHappening) {
            throw new Error("Response acceptance: Clarity without what-is-happening rejected");
        }
        const matters = composed.what_matters_now.toLowerCase();
        const ask = (composed.still_unclear?.[0] ?? "").toLowerCase().replace(/\?$/, "");
        if (ask.length > 12 && matters.includes(ask.slice(0, 24))) {
            throw new Error("Response acceptance: what_matters_now must not echo the ask field");
        }
        if ((composed.follow_up_items?.length ?? 0) === 0 && turnClass !== "improvement") {
            throw new Error("Response acceptance: Clarity without follow-up continuity rejected");
        }
    }
    // New user: never fake continuity
    if (careMemoryState === "new_care_reality" && !skipStructure) {
        if ((0, care_memory_maturity_1.containsFakeContinuity)(blob)) {
            throw new Error("Response acceptance: fake continuity on new care reality rejected");
        }
        if (composed.connection_note?.trim()) {
            throw new Error("Response acceptance: connection note on first capture rejected");
        }
    }
    // Never treat product/session meta as care memory in caregiver-facing fields
    for (const line of [
        ...(composed.what_we_know ?? []),
        composed.connection_note ?? "",
        composed.what_changed ?? "",
        composed.evidence_line ?? "",
    ]) {
        if (line && (0, care_epistemics_1.isProductSessionMetaText)(line.replace(/^(already held|earlier|new):\s*/i, ""))) {
            throw new Error("Response acceptance: product/session meta surfaced as care reality");
        }
        if (/\b(hi|hello|hey)\s+solenos\b/i.test(line) ||
            (/\bfirst time here\b/i.test(line) && /\bsolenos\b/i.test(line))) {
            throw new Error("Response acceptance: product greeting cited as care memory");
        }
    }
    // Returning: must show relationship to prior story when multiple observations
    if (careMemoryState === "returning_care_reality" &&
        observationCount > 1 &&
        !skipStructure &&
        turnClass !== "improvement") {
        const hasConnection = Boolean(composed.connection_note?.trim()) ||
            Boolean(composed.what_changed?.trim()) ||
            composed.what_we_know.some((l) => /already held|connects|earlier held|new:/i.test(l));
        if (!hasConnection) {
            throw new Error("Response acceptance: returning user missing connection to prior care story");
        }
    }
    // Echo-only: repeating caregiver text without adding structure
    const latest = params.latestRawText.trim().toLowerCase();
    if (latest.length > 20 &&
        composed.what_we_know.length === 1 &&
        composed.what_we_know[0].toLowerCase().includes(latest.slice(0, Math.min(40, latest.length))) &&
        !composed.what_changed &&
        !composed.connection_note &&
        !composed.situation_summary) {
        throw new Error("Response acceptance: echo without understanding rejected");
    }
    // Raw-note paste theater — recognition / matters / connection / evidence / follow-ups / what_changed.
    // care_story_update may restate a held decision/fact briefly; checked via templates below.
    if (params.latestRawText.trim().length >= 40) {
        const pasteSurface = [
            composed.recognition_line ?? "",
            composed.what_matters_now ?? "",
            composed.connection_note ?? "",
            composed.evidence_line ?? "",
            composed.what_changed ?? "",
            ...(composed.follow_up_items ?? []),
        ].join("\n");
        if ((0, output_quality_1.containsRawNoteEchoInCopy)({
            blob: pasteSurface,
            latestRawText: params.latestRawText,
        })) {
            throw new Error("Response acceptance: raw note echo theater rejected");
        }
        if (/first timeline entry(?:\s+for\s+[\w'’]+)?:\s*[^.\n]{75,}/i.test(composed.care_story_update ?? "")) {
            throw new Error("Response acceptance: raw note echo theater rejected");
        }
    }
    if (!skipStructure && !awaitingCare) {
        const understanding = (0, caregiver_understanding_output_1.evaluateCaregiverUnderstandingOutput)(composed);
        if (!understanding.passed) {
            throw new Error(`Response acceptance: understanding-not-summary failed (${understanding.failures.join("; ")})`);
        }
    }
    void lower;
}
/** Non-throwing evaluate for tests / telemetry. */
function evaluateResponseAcceptance(params) {
    const failures = [];
    try {
        assertResponseAcceptanceGate(params);
    }
    catch (err) {
        failures.push(err instanceof Error ? err.message : String(err));
    }
    return { passed: failures.length === 0, failures };
}
