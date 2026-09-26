"use strict";
/**
 * Care Situation Understanding — meaning before caregiver language.
 *
 * Raw input (text / OCR from Scan·Snap·Upload·Share) → typed care reality →
 * prioritize → memory hooks → Response Contract projection.
 *
 * Instant-value rule: sync deterministic path must orient on first capture.
 * Optional LLM enrichment never blocks first orientation (fail-closed).
 *
 * Doc examples / golden fixtures = evaluation only — never product if-branches.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.INSTANT_VALUE_RULE = exports.CARE_SITUATION_UNDERSTANDING_PURPOSE = void 0;
exports.CARE_SITUATION_UNDERSTANDING_PURPOSE = "Transform messy caregiver input into structured care understanding before responding — never summarize.";
exports.INSTANT_VALUE_RULE = "First capture must make the situation clearer in one glance (~30s). No setup homework. No waiting on enrichment to orient.";
