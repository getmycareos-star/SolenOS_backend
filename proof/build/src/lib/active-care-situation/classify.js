"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.detectSubjectLabel = void 0;
exports.isSoftObservationKind = isSoftObservationKind;
exports.isHardEventKind = isHardEventKind;
exports.isEmotionalOrBehavioralText = isEmotionalOrBehavioralText;
exports.humanFactFromObservation = humanFactFromObservation;
exports.refineHumanFact = refineHumanFact;
exports.sameCalendarDay = sameCalendarDay;
exports.withinActiveWindow = withinActiveWindow;
exports.subjectsCompatible = subjectsCompatible;
exports.referencesHardEventInText = referencesHardEventInText;
exports.classifySituationRelation = classifySituationRelation;
exports.situationThemeFor = situationThemeFor;
const contract_constants_1 = require("./contract-constants");
const clarity_pillars_1 = require("../progressive-understanding/clarity-pillars");
const care_epistemics_1 = require("../care-epistemics");
const situation_relationship_engine_1 = require("../situation-relationship-engine");
const EMOTIONAL = /\b(frustrat\w*|sad|upset|angry|anxious|anxious|scared|lonely|distressed|agitated|mood|crying|tear\w*|want(?:s|ed)? to go home|go home|homesick)\b/i;
/** @deprecated Prefer resolveSubjectLabel — never infer kinship from note text. */
var signals_1 = require("../situation-relationship-engine/signals");
Object.defineProperty(exports, "detectSubjectLabel", { enumerable: true, get: function () { return signals_1.detectSubjectLabel; } });
function isSoftObservationKind(kind) {
    return contract_constants_1.ACTIVE_CARE_SITUATION_SOFT_KINDS.includes(kind);
}
function isHardEventKind(kind) {
    return contract_constants_1.ACTIVE_CARE_SITUATION_HARD_KINDS.includes(kind);
}
function isEmotionalOrBehavioralText(text) {
    return EMOTIONAL.test(text);
}
function humanFactFromObservation(text, subject) {
    const cleaned = text.trim().replace(/^["']+|["']+$/g, "");
    // Pure Continuity Demand — never invent a Dad/Mom "fact" from guidance alone.
    if ((0, clarity_pillars_1.isCaregiverGuidanceDemand)(cleaned))
        return "";
    // Mixed care + guidance: hold the care remainder, drop the ask.
    const stripped = (0, clarity_pillars_1.stripCaregiverGuidancePhrases)(cleaned);
    const careText = stripped.length >= 8 ? stripped : cleaned;
    // Keep caregiver words — never canned kinship scripts (go home / frustrated / feeling better).
    const who = subject === "Mom"
        ? "Your mom"
        : subject === "Dad"
            ? "Your dad"
            : subject && subject !== "Your loved one" && subject !== "they" && subject !== "person"
                ? subject
                : null;
    const clipped = careText.length > 120 ? `${careText.slice(0, 117)}…` : careText;
    if (/^[A-Z]/.test(clipped))
        return clipped.endsWith(".") ? clipped : `${clipped}.`;
    if (who)
        return `${who}: ${clipped}${clipped.endsWith(".") ? "" : "."}`;
    return clipped.endsWith(".") ? clipped : `${clipped}.`;
}
/** Caregiver-facing fact line for an observation. */
function refineHumanFact(text, subject, options) {
    void options;
    if ((0, clarity_pillars_1.isCaregiverGuidanceDemand)(text))
        return "";
    const stripped = (0, clarity_pillars_1.stripCaregiverGuidancePhrases)(text.trim());
    const source = stripped.length >= 8 ? stripped : text;
    // Product / session meta is not a care fact — do not promote into Living Care Record facts.
    if ((0, care_epistemics_1.isProductSessionMetaText)(source))
        return "";
    if ((0, care_epistemics_1.looksLikeCaregiverMissedCareAction)(source)) {
        return (0, care_epistemics_1.frameMissedCareHumanFact)(source);
    }
    const epistemic = (0, care_epistemics_1.classifyEpistemicClaim)(source);
    if (epistemic === "caregiver_interpretation") {
        return (0, care_epistemics_1.frameInterpretationHumanFact)(source, subject);
    }
    // Improvement and mood notes: preserve caregiver words — never invent wellness theater.
    return humanFactFromObservation(source, subject);
}
function sameCalendarDay(aIso, bIso) {
    const a = new Date(aIso);
    const b = new Date(bIso);
    return (a.getFullYear() === b.getFullYear() &&
        a.getMonth() === b.getMonth() &&
        a.getDate() === b.getDate());
}
function withinActiveWindow(updatedAt, nowIso) {
    const delta = new Date(nowIso).getTime() - new Date(updatedAt).getTime();
    return delta >= 0 && delta <= contract_constants_1.ACTIVE_CARE_SITUATION_WINDOW_MS;
}
function subjectsCompatible(a, b) {
    if (a === b)
        return true;
    if (a === "Your loved one" || b === "Your loved one" || a === "they" || b === "they")
        return true;
    return false;
}
/** True when the note itself is a hard-event follow-up (safe to attach to hard ACS). */
function referencesHardEventInText(text) {
    // Intentional hard follow-up only — bare head/hurt/hospital/pill must not glue mood notes.
    return (/\bfell\b|\bfall\b|\bfallen\b|\btripped\b|\bslipped\b/i.test(text) ||
        /\bhit\s+(her|his|their)\s+head\b|\bhead\s+injur/i.test(text) ||
        /\burgent\s+care\b|\bdischarg/i.test(text) ||
        /\b(medication|medicine|prescription|dose|pill|pills|rx)\b/i.test(text) ||
        /\bappointment\b|\bfollow[- ]?up\b/i.test(text));
}
/**
 * Server-owned continuity: same situation, or a completely different event?
 * Delegates to Situation Relationship Engine (Product Steward SoT).
 * Client entryIntent is ignored — relation comes from ACS state + content only.
 */
function classifySituationRelation(params) {
    return (0, situation_relationship_engine_1.evaluateSituationRelationship)({
        active: params.active,
        rawText: params.rawText,
        kind: params.kind,
        nowIso: params.nowIso,
    }).acs_relation;
}
function situationThemeFor(kind, text) {
    if (isEmotionalOrBehavioralText(text) || kind === "behavior_change") {
        return "emotional_behavior";
    }
    if (kind === "fall" || kind === "hospital_discharge")
        return "incident";
    if (kind === "medication_change" || kind === "appointment" || kind === "document") {
        return "care_change";
    }
    return "mixed";
}
