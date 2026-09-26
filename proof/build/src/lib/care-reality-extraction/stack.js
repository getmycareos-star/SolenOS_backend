"use strict";
/**
 * Care Reality extraction stack — Observation → Event → Decision → Relationship → Response Contract.
 * Unknown is the knowledge-boundary layer that runs alongside — never fill gaps.
 *
 * SoT: docs/02-product/solenos-*-extraction.md · solenos-response-contract.md
 * Doc examples are illustrations only — never product if-branches.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.EXTRACTION_STACK_PURPOSE = exports.EXTRACTION_STACK_ASKS = exports.EXTRACTION_STACK_PIPELINE = void 0;
const decisions_1 = require("./decisions");
const relationships_1 = require("./relationships");
const unknowns_1 = require("./unknowns");
exports.EXTRACTION_STACK_PIPELINE = [
    "observation",
    "event",
    "decision",
    "relationship",
    "response_contract",
];
/** Core asks — Relationship is fourth; Unknown preserves knowledge boundaries throughout. */
exports.EXTRACTION_STACK_ASKS = {
    observation: "What was directly witnessed about the person receiving care?",
    event: "What happened, when, who was involved?",
    decision: decisions_1.DECISION_EXTRACTION_ASK,
    relationship: relationships_1.RELATIONSHIP_EXTRACTION_ASK,
    unknown: unknowns_1.UNKNOWN_EXTRACTION_ASK,
};
exports.EXTRACTION_STACK_PURPOSE = "Observation → Event → Decision → Relationship → Response Contract — with Unknown preserving what is not known (never fill gaps).";
