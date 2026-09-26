"use strict";
/**
 * Care Reality extraction — Observation / Event / Decision / Outcome / Unknown / Relationship.
 *
 * SoT: docs/02-product/solenos-*-extraction.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.createExtractedEvent = exports.normalizeOutcomeDescription = exports.looksLikeInterpretationWithoutEvidence = exports.looksLikeIntentionNotOutcome = exports.looksLikeOutcomeFragment = exports.composeCaregiverOutcomeLine = exports.createExtractedOutcome = exports.UNKNOWN_STATUS_LEAKAGE_PATTERNS = exports.assertUnknownPreservation = exports.validateUnknownPreservation = exports.containsUnknownStatusLeakage = exports.UNKNOWN_EXTRACTION_CORE = exports.UNKNOWN_EXTRACTION_NEVER_ASK = exports.UNKNOWN_EXTRACTION_ASK = exports.dedupeExtractedUnknowns = exports.normalizeUnknownQuestion = exports.looksLikeInventedCertaintyFromUncertainty = exports.composeCaregiverUnknownAsk = exports.createExtractedUnknown = exports.applySessionKinshipDisplay = exports.caregiverFacingLinesFromCaptureText = exports.caregiverFacingLinesFromExtraction = exports.splitExtractionFragments = exports.splitCompoundCareClauses = exports.extractCareRealityFromText = exports.RELATIONSHIP_CAUSATION_THEATER_PATTERNS = exports.RELATIONSHIP_ENUM_LEAKAGE_PATTERNS = exports.containsRelationshipCausationTheater = exports.containsRelationshipEnumLeakage = exports.composeCaregiverConnectionFromRelationships = exports.proposeExtractionRelationships = exports.RELATIONSHIP_EXTRACTION_NEVER_ASK = exports.RELATIONSHIP_EXTRACTION_ASK = exports.EXTRACTION_STACK_PURPOSE = exports.EXTRACTION_STACK_PIPELINE = exports.EXTRACTION_STACK_ASKS = exports.isExtractableDecisionFragment = exports.decisionWho = exports.decisionWhy = exports.looksLikeRecommendationNotDecision = exports.linkDecisionEvidence = exports.createExtractedDecision = exports.DECISION_EXTRACTION_NEVER_ASK = exports.DECISION_EXTRACTION_ASK = exports.classifyExtractionFragment = exports.looksLikeOpenUnknownFragment = exports.looksLikeDisagreementPerspectiveFragment = exports.looksLikeContributorLoadFragment = exports.looksLikeCareDecisionFragment = exports.CARE_REALITY_EXTRACTION_PURPOSE = void 0;
exports.isActionNotOutcome = exports.actionWho = exports.createExtractedAction = exports.looksLikeCareActionFragment = exports.ACTION_EXTRACTION_NEVER_ASK = exports.ACTION_EXTRACTION_ASK = exports.looksLikeCareJourneyEventFragment = exports.looksLikeIntentionNotEvent = exports.extractEventParticipants = exports.normalizeEventDescription = void 0;
exports.isNonObservationFocusLine = isNonObservationFocusLine;
exports.CARE_REALITY_EXTRACTION_PURPOSE = "Observation → Event → Decision → Relationship → Response Contract — Unknown preserves knowledge boundaries; never invent facts, keyword-only links, or causation theater.";
var classify_1 = require("./classify");
Object.defineProperty(exports, "looksLikeCareDecisionFragment", { enumerable: true, get: function () { return classify_1.looksLikeCareDecisionFragment; } });
Object.defineProperty(exports, "looksLikeContributorLoadFragment", { enumerable: true, get: function () { return classify_1.looksLikeContributorLoadFragment; } });
Object.defineProperty(exports, "looksLikeDisagreementPerspectiveFragment", { enumerable: true, get: function () { return classify_1.looksLikeDisagreementPerspectiveFragment; } });
Object.defineProperty(exports, "looksLikeOpenUnknownFragment", { enumerable: true, get: function () { return classify_1.looksLikeOpenUnknownFragment; } });
Object.defineProperty(exports, "classifyExtractionFragment", { enumerable: true, get: function () { return classify_1.classifyExtractionFragment; } });
var decisions_1 = require("./decisions");
Object.defineProperty(exports, "DECISION_EXTRACTION_ASK", { enumerable: true, get: function () { return decisions_1.DECISION_EXTRACTION_ASK; } });
Object.defineProperty(exports, "DECISION_EXTRACTION_NEVER_ASK", { enumerable: true, get: function () { return decisions_1.DECISION_EXTRACTION_NEVER_ASK; } });
Object.defineProperty(exports, "createExtractedDecision", { enumerable: true, get: function () { return decisions_1.createExtractedDecision; } });
Object.defineProperty(exports, "linkDecisionEvidence", { enumerable: true, get: function () { return decisions_1.linkDecisionEvidence; } });
Object.defineProperty(exports, "looksLikeRecommendationNotDecision", { enumerable: true, get: function () { return decisions_1.looksLikeRecommendationNotDecision; } });
Object.defineProperty(exports, "decisionWhy", { enumerable: true, get: function () { return decisions_1.decisionWhy; } });
Object.defineProperty(exports, "decisionWho", { enumerable: true, get: function () { return decisions_1.decisionWho; } });
Object.defineProperty(exports, "isExtractableDecisionFragment", { enumerable: true, get: function () { return decisions_1.isExtractableDecisionFragment; } });
var stack_1 = require("./stack");
Object.defineProperty(exports, "EXTRACTION_STACK_ASKS", { enumerable: true, get: function () { return stack_1.EXTRACTION_STACK_ASKS; } });
Object.defineProperty(exports, "EXTRACTION_STACK_PIPELINE", { enumerable: true, get: function () { return stack_1.EXTRACTION_STACK_PIPELINE; } });
Object.defineProperty(exports, "EXTRACTION_STACK_PURPOSE", { enumerable: true, get: function () { return stack_1.EXTRACTION_STACK_PURPOSE; } });
var relationships_1 = require("./relationships");
Object.defineProperty(exports, "RELATIONSHIP_EXTRACTION_ASK", { enumerable: true, get: function () { return relationships_1.RELATIONSHIP_EXTRACTION_ASK; } });
Object.defineProperty(exports, "RELATIONSHIP_EXTRACTION_NEVER_ASK", { enumerable: true, get: function () { return relationships_1.RELATIONSHIP_EXTRACTION_NEVER_ASK; } });
Object.defineProperty(exports, "proposeExtractionRelationships", { enumerable: true, get: function () { return relationships_1.proposeExtractionRelationships; } });
Object.defineProperty(exports, "composeCaregiverConnectionFromRelationships", { enumerable: true, get: function () { return relationships_1.composeCaregiverConnectionFromRelationships; } });
Object.defineProperty(exports, "containsRelationshipEnumLeakage", { enumerable: true, get: function () { return relationships_1.containsRelationshipEnumLeakage; } });
Object.defineProperty(exports, "containsRelationshipCausationTheater", { enumerable: true, get: function () { return relationships_1.containsRelationshipCausationTheater; } });
Object.defineProperty(exports, "RELATIONSHIP_ENUM_LEAKAGE_PATTERNS", { enumerable: true, get: function () { return relationships_1.RELATIONSHIP_ENUM_LEAKAGE_PATTERNS; } });
Object.defineProperty(exports, "RELATIONSHIP_CAUSATION_THEATER_PATTERNS", { enumerable: true, get: function () { return relationships_1.RELATIONSHIP_CAUSATION_THEATER_PATTERNS; } });
var extract_1 = require("./extract");
Object.defineProperty(exports, "extractCareRealityFromText", { enumerable: true, get: function () { return extract_1.extractCareRealityFromText; } });
Object.defineProperty(exports, "splitCompoundCareClauses", { enumerable: true, get: function () { return extract_1.splitCompoundCareClauses; } });
Object.defineProperty(exports, "splitExtractionFragments", { enumerable: true, get: function () { return extract_1.splitExtractionFragments; } });
var caregiver_surfaces_1 = require("./caregiver-surfaces");
Object.defineProperty(exports, "caregiverFacingLinesFromExtraction", { enumerable: true, get: function () { return caregiver_surfaces_1.caregiverFacingLinesFromExtraction; } });
Object.defineProperty(exports, "caregiverFacingLinesFromCaptureText", { enumerable: true, get: function () { return caregiver_surfaces_1.caregiverFacingLinesFromCaptureText; } });
Object.defineProperty(exports, "applySessionKinshipDisplay", { enumerable: true, get: function () { return caregiver_surfaces_1.applySessionKinshipDisplay; } });
var unknowns_1 = require("./unknowns");
Object.defineProperty(exports, "createExtractedUnknown", { enumerable: true, get: function () { return unknowns_1.createExtractedUnknown; } });
Object.defineProperty(exports, "composeCaregiverUnknownAsk", { enumerable: true, get: function () { return unknowns_1.composeCaregiverUnknownAsk; } });
Object.defineProperty(exports, "looksLikeInventedCertaintyFromUncertainty", { enumerable: true, get: function () { return unknowns_1.looksLikeInventedCertaintyFromUncertainty; } });
Object.defineProperty(exports, "normalizeUnknownQuestion", { enumerable: true, get: function () { return unknowns_1.normalizeUnknownQuestion; } });
Object.defineProperty(exports, "dedupeExtractedUnknowns", { enumerable: true, get: function () { return unknowns_1.dedupeExtractedUnknowns; } });
Object.defineProperty(exports, "UNKNOWN_EXTRACTION_ASK", { enumerable: true, get: function () { return unknowns_1.UNKNOWN_EXTRACTION_ASK; } });
Object.defineProperty(exports, "UNKNOWN_EXTRACTION_NEVER_ASK", { enumerable: true, get: function () { return unknowns_1.UNKNOWN_EXTRACTION_NEVER_ASK; } });
Object.defineProperty(exports, "UNKNOWN_EXTRACTION_CORE", { enumerable: true, get: function () { return unknowns_1.UNKNOWN_EXTRACTION_CORE; } });
Object.defineProperty(exports, "containsUnknownStatusLeakage", { enumerable: true, get: function () { return unknowns_1.containsUnknownStatusLeakage; } });
Object.defineProperty(exports, "validateUnknownPreservation", { enumerable: true, get: function () { return unknowns_1.validateUnknownPreservation; } });
Object.defineProperty(exports, "assertUnknownPreservation", { enumerable: true, get: function () { return unknowns_1.assertUnknownPreservation; } });
Object.defineProperty(exports, "UNKNOWN_STATUS_LEAKAGE_PATTERNS", { enumerable: true, get: function () { return unknowns_1.UNKNOWN_STATUS_LEAKAGE_PATTERNS; } });
var outcomes_1 = require("./outcomes");
Object.defineProperty(exports, "createExtractedOutcome", { enumerable: true, get: function () { return outcomes_1.createExtractedOutcome; } });
Object.defineProperty(exports, "composeCaregiverOutcomeLine", { enumerable: true, get: function () { return outcomes_1.composeCaregiverOutcomeLine; } });
Object.defineProperty(exports, "looksLikeOutcomeFragment", { enumerable: true, get: function () { return outcomes_1.looksLikeOutcomeFragment; } });
Object.defineProperty(exports, "looksLikeIntentionNotOutcome", { enumerable: true, get: function () { return outcomes_1.looksLikeIntentionNotOutcome; } });
Object.defineProperty(exports, "looksLikeInterpretationWithoutEvidence", { enumerable: true, get: function () { return outcomes_1.looksLikeInterpretationWithoutEvidence; } });
Object.defineProperty(exports, "normalizeOutcomeDescription", { enumerable: true, get: function () { return outcomes_1.normalizeOutcomeDescription; } });
var events_1 = require("./events");
Object.defineProperty(exports, "createExtractedEvent", { enumerable: true, get: function () { return events_1.createExtractedEvent; } });
Object.defineProperty(exports, "normalizeEventDescription", { enumerable: true, get: function () { return events_1.normalizeEventDescription; } });
Object.defineProperty(exports, "extractEventParticipants", { enumerable: true, get: function () { return events_1.extractEventParticipants; } });
Object.defineProperty(exports, "looksLikeIntentionNotEvent", { enumerable: true, get: function () { return events_1.looksLikeIntentionNotEvent; } });
Object.defineProperty(exports, "looksLikeCareJourneyEventFragment", { enumerable: true, get: function () { return events_1.looksLikeCareJourneyEventFragment; } });
var actions_1 = require("./actions");
Object.defineProperty(exports, "ACTION_EXTRACTION_ASK", { enumerable: true, get: function () { return actions_1.ACTION_EXTRACTION_ASK; } });
Object.defineProperty(exports, "ACTION_EXTRACTION_NEVER_ASK", { enumerable: true, get: function () { return actions_1.ACTION_EXTRACTION_NEVER_ASK; } });
Object.defineProperty(exports, "looksLikeCareActionFragment", { enumerable: true, get: function () { return actions_1.looksLikeCareActionFragment; } });
Object.defineProperty(exports, "createExtractedAction", { enumerable: true, get: function () { return actions_1.createExtractedAction; } });
Object.defineProperty(exports, "actionWho", { enumerable: true, get: function () { return actions_1.actionWho; } });
Object.defineProperty(exports, "isActionNotOutcome", { enumerable: true, get: function () { return actions_1.isActionNotOutcome; } });
const classify_2 = require("./classify");
/** True when a line must not drive standout / What matters (load or disagreement). */
function isNonObservationFocusLine(text) {
    return ((0, classify_2.looksLikeContributorLoadFragment)(text) ||
        (0, classify_2.looksLikeDisagreementPerspectiveFragment)(text));
}
