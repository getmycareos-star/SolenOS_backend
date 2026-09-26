"use strict";
/**
 * Caregiver-facing understanding output — not document/chat summarization.
 * SoT: docs/02-product/solenos-communicate-understanding.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.UNDERSTANDING_NOT_SUMMARY_NEVER_ASK = exports.UNDERSTANDING_NOT_SUMMARY_ASK = exports.ECHO_SUMMARY_THEATER = exports.DOCUMENT_SUMMARIZER_THEATER = exports.CAREGIVER_UNDERSTANDING_FIELDS = exports.CAREGIVER_UNDERSTANDING_OUTPUT_PURPOSE = void 0;
exports.containsDocumentSummarizerTheater = containsDocumentSummarizerTheater;
exports.containsEchoSummaryTheater = containsEchoSummaryTheater;
exports.evaluateCaregiverUnderstandingOutput = evaluateCaregiverUnderstandingOutput;
exports.CAREGIVER_UNDERSTANDING_OUTPUT_PURPOSE = "Communicate care-situation understanding — never summarize documents or echo input as the product.";
/** Required caregiver orientation fields (disclosure may hide; compose must produce). */
exports.CAREGIVER_UNDERSTANDING_FIELDS = [
    "current_understanding",
    "what_changed",
    "what_matters_now",
    "what_is_unclear",
    "questions_worth_answering",
];
/** Document / ChatGPT summarizer theater — reject in caregiver copy. */
exports.DOCUMENT_SUMMARIZER_THEATER = [
    /here is (?:a |your )?summary of (?:your )?(?:document|pdf|file|upload)/i,
    /here'?s (?:a |your )?summary of/i,
    /\bsummary:\s*(?:patient|mom|dad|the document)/i,
    /\bdocument summary\b/i,
    /\bi (?:have )?extracted (?:the )?(?:following|key)/i,
    /\bkey (?:points|takeaways) from (?:your )?(?:document|upload|pdf)\b/i,
    /\bthe (?:document|pdf|file) (?:says|states|indicates|shows)\b/i,
    /\bbased on (?:the |your )?(?:document|pdf|upload)\b/i,
    /\bin summary[,:]/i,
    /\bto summarize[,:]/i,
    /\btl;?dr\b/i,
    /here'?s my understanding/i,
    /putting this together/i,
    /i'?m hearing that/i,
    /key takeaways/i,
    /here'?s the gist/i,
];
/** Echo / paraphrase-as-product — weak understanding. */
exports.ECHO_SUMMARY_THEATER = [
    /\byou (?:said|wrote|mentioned) that\b.{0,80}\byou (?:said|wrote|mentioned)\b/i,
    /\brepeating what you (?:shared|wrote)\b/i,
    /\bas you (?:stated|noted)[,:]/i,
    /\bin your (?:own )?words[,:]/i,
];
exports.UNDERSTANDING_NOT_SUMMARY_ASK = "What does this information change about our understanding of this person's care?";
exports.UNDERSTANDING_NOT_SUMMARY_NEVER_ASK = "What should I say about this input?";
function containsDocumentSummarizerTheater(blob) {
    return exports.DOCUMENT_SUMMARIZER_THEATER.some((p) => p.test(blob));
}
function containsEchoSummaryTheater(blob) {
    return exports.ECHO_SUMMARY_THEATER.some((p) => p.test(blob));
}
/**
 * Structural check: composed response has understanding substance, not only a hold confirmation.
 */
function evaluateCaregiverUnderstandingOutput(composed) {
    const failures = [];
    const blob = [
        composed.recognition_line ?? "",
        composed.confirmation,
        composed.situation_summary ?? "",
        ...(composed.what_we_know ?? []),
        composed.what_changed ?? "",
        composed.connection_note ?? "",
        composed.what_matters_now ?? "",
        ...(composed.still_unclear ?? []),
        composed.care_story_update ?? "",
    ].join("\n");
    if (containsDocumentSummarizerTheater(blob)) {
        failures.push("document summarizer theater");
    }
    if (containsEchoSummaryTheater(blob)) {
        failures.push("echo summary theater");
    }
    const hasUnderstanding = (composed.what_we_know?.length ?? 0) > 0 ||
        Boolean(composed.situation_summary?.trim()) ||
        Boolean(composed.what_changed?.trim()) ||
        (Boolean(composed.what_matters_now?.trim()) &&
            (composed.still_unclear?.length ?? 0) > 0);
    if (!hasUnderstanding) {
        failures.push("missing current understanding substance");
    }
    // Recognition alone is never enough — that is AI acknowledgment, not care understanding.
    if (composed.recognition_line?.trim() &&
        (composed.what_we_know?.length ?? 0) === 0 &&
        !composed.situation_summary?.trim() &&
        !composed.what_changed?.trim() &&
        !composed.what_matters_now?.trim() &&
        (composed.still_unclear?.length ?? 0) === 0) {
        failures.push("recognition-only output without care understanding");
    }
    // Document/care captures should not end as confirmation-only
    if (!composed.situation_summary &&
        (composed.what_we_know?.length ?? 0) === 0 &&
        !composed.what_changed &&
        !composed.still_unclear?.length &&
        composed.confirmation &&
        /summary|extracted|key points/i.test(composed.confirmation)) {
        failures.push("confirmation looks like a summary endpoint");
    }
    return { passed: failures.length === 0, failures };
}
