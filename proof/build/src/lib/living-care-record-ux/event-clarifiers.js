"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.classifyCareEventKind = classifyCareEventKind;
exports.clarificationQuestionsForKind = clarificationQuestionsForKind;
exports.rememberedThemesForKind = rememberedThemesForKind;
exports.eventTypeLabel = eventTypeLabel;
const FALL = /\b(fell|fall|fallen|tripped|slipped)\b/i;
/** Require medication lexicon — bare started/stopped/changed is not a med change. */
const MED = /\b(medication|medicine|medications|medicines|prescription|dose|doses|pill|pills|rx)\b|\b(started|stopped|changed)\s+(her|his|their|the|a|an)\s+\w*(med|pill|dose|rx)/i;
const BEHAVIOR = /\b(confus\w*|agitated|wandering|aggressive|behavior|mood|frustrat\w*|sad|upset|angry|anxious|scared|lonely|distressed|crying|want(?:s|ed)? to go home|go home|homesick)\b/i;
const DISCHARGE = /\b(discharge|discharged|sent home|left the hospital)\b/i;
const APPETITE = /\b(refus\w*\s+to\s+eat|not eating|won't eat|appetite|food|meal)\b/i;
const APPOINTMENT = /\b(appointment|follow[- ]?up|clinic|doctor visit)\b/i;
function classifyCareEventKind(text, extractedType, fromDocument) {
    // Documents still get clinical kind when content is discharge/appointment —
    // Input Reality: clinical artifacts ≠ memory notes.
    if (DISCHARGE.test(text))
        return "hospital_discharge";
    if (FALL.test(text))
        return "fall";
    if (MED.test(text))
        return "medication_change";
    if (BEHAVIOR.test(text) || extractedType === "behavioral_change")
        return "behavior_change";
    if (APPETITE.test(text))
        return "appetite";
    if (APPOINTMENT.test(text) || extractedType === "follow_up")
        return "appointment";
    if (fromDocument || extractedType === "document_fact")
        return "document";
    return "general";
}
/**
 * Kind-hinted clarification candidates — always empty.
 * Asks come from understanding gaps (generic gather), never topic templates
 * keyed off fall / appetite / med / discharge keywords.
 */
function clarificationQuestionsForKind(_kind) {
    return [];
}
/**
 * Generic continuity themes — never kind-keyed banks (fall/appetite/med templates).
 */
function rememberedThemesForKind(_kind) {
    return [
        "Care timeline continuity",
        "What changed over time",
        "Open questions that still need context",
    ];
}
function eventTypeLabel(kind) {
    switch (kind) {
        case "fall":
            return "Fall";
        case "medication_change":
            return "Medication change";
        case "behavior_change":
            return "Behavior change";
        case "hospital_discharge":
            return "Hospital discharge";
        case "appetite":
            return "Eating / appetite";
        case "appointment":
            return "Appointment / visit";
        case "document":
            return "Document";
        default:
            return "Care observation";
    }
}
