"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.BOUNDARIES_DEFINING_PRINCIPLE = exports.BOUNDARIES_IDENTITY = void 0;
exports.enforceArchitecturalBoundaries = enforceArchitecturalBoundaries;
exports.enforceBoundariesOnFinalOutput = enforceBoundariesOnFinalOutput;
const contract_constants_1 = require("./contract-constants");
Object.defineProperty(exports, "BOUNDARIES_DEFINING_PRINCIPLE", { enumerable: true, get: function () { return contract_constants_1.BOUNDARIES_DEFINING_PRINCIPLE; } });
Object.defineProperty(exports, "BOUNDARIES_IDENTITY", { enumerable: true, get: function () { return contract_constants_1.BOUNDARIES_IDENTITY; } });
const detect_violations_1 = require("./detect-violations");
const store_1 = require("./store");
function enforceArchitecturalBoundaries(input) {
    const violations = (0, detect_violations_1.scanAllSurfaces)(input.text_surfaces);
    const rulesSatisfied = [];
    if (violations.filter((v) => v.rule === "never_diagnose").length === 0) {
        rulesSatisfied.push("never_diagnose");
    }
    if (violations.filter((v) => v.rule === "never_invent_information").length === 0) {
        rulesSatisfied.push("never_invent_information");
    }
    if (input.has_explicit_uncertainty)
        rulesSatisfied.push("never_hide_uncertainty");
    if (input.preserves_history)
        rulesSatisfied.push("never_overwrite_history");
    if (input.confidence_proportional)
        rulesSatisfied.push("never_pretend_confidence");
    if (violations.filter((v) => v.rule === "never_optimize_for_engagement").length === 0) {
        rulesSatisfied.push("never_optimize_for_engagement");
    }
    if (input.has_evidence_links && input.has_decision_trace) {
        rulesSatisfied.push("never_separate_observations_from_evidence");
    }
    if (input.preserves_history)
        rulesSatisfied.push("never_destroy_continuity");
    rulesSatisfied.push("never_replace_clinical_judgment");
    if (violations.length === 0 || input.has_explicit_uncertainty) {
        rulesSatisfied.push("never_prioritize_automation_over_accuracy");
    }
    const uniqueSatisfied = [...new Set(rulesSatisfied)];
    (0, store_1.recordBoundaryAudit)({
        violations_count: violations.length,
        rules_satisfied: uniqueSatisfied.length,
    });
    return {
        enforced: true,
        rules_checked: [...contract_constants_1.ARCHITECTURAL_RULES],
        rules_satisfied: uniqueSatisfied,
        violations_detected: violations,
        violations_remediated: violations.filter((v) => v.severity === "critical"),
        decision_framework_passed: violations.filter((v) => v.severity === "critical").length === 0,
        defining_principle: contract_constants_1.BOUNDARIES_DEFINING_PRINCIPLE,
        prohibited_avoided: Object.values(contract_constants_1.RULE_DEFINITIONS),
    };
}
function enforceBoundariesOnFinalOutput(output, meta) {
    const surfaces = {
        what_is_happening: output.what_is_happening,
        what_matters_now: output.what_matters_now,
        what_to_ask_next: output.what_to_ask_next,
        what_can_wait: output.what_can_wait,
        follow_up_items: output.follow_up_items,
        decision_trace_events: output.decision_trace.events,
        decision_trace_assumptions: output.decision_trace.assumptions,
        decision_trace_unknowns: output.decision_trace.unknowns,
    };
    const boundaries = enforceArchitecturalBoundaries({
        text_surfaces: surfaces,
        ...meta,
    });
    if (boundaries.violations_detected.length === 0) {
        return { output, boundaries };
    }
    const remediated = { ...output };
    const fix = (s) => (0, detect_violations_1.remediateText)(s).text;
    remediated.what_is_happening = fix(output.what_is_happening);
    remediated.what_matters_now = fix(output.what_matters_now);
    remediated.what_to_ask_next = fix(output.what_to_ask_next);
    remediated.follow_up_items = output.follow_up_items.map(fix);
    return { output: remediated, boundaries };
}
