"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.FINAL_OUTPUT_CONTRACT_IDENTITY = void 0;
exports.processFinalOutput = processFinalOutput;
exports.enforceFinalOutputAtBoundary = enforceFinalOutputAtBoundary;
const contract_constants_1 = require("./contract-constants");
Object.defineProperty(exports, "FINAL_OUTPUT_CONTRACT_IDENTITY", { enumerable: true, get: function () { return contract_constants_1.FINAL_OUTPUT_CONTRACT_IDENTITY; } });
const compile_1 = require("./compile");
const schema_1 = require("./schema");
const architectural_boundaries_1 = require("../architectural-boundaries");
function processFinalOutput(situationResponse) {
    const compiled = (0, compile_1.compileFromSituationResponse)(situationResponse);
    return (0, schema_1.validateFinalOutput)(compiled);
}
function enforceFinalOutputAtBoundary(output, source) {
    const validated = (0, schema_1.validateFinalOutput)(output);
    const { output: bounded, boundaries } = (0, architectural_boundaries_1.enforceBoundariesOnFinalOutput)(validated, {
        has_decision_trace: validated.decision_trace.events.length > 0,
        has_evidence_links: validated.decision_trace.evidence_sources.length > 0,
        has_explicit_uncertainty: validated.decision_trace.unknowns.length > 0,
        preserves_history: (source?.context.events.length ?? 0) > 0,
        confidence_proportional: validated.confidence_state.overall_confidence !== "high" ||
            validated.confidence_state.completeness >= 60,
    });
    return {
        final_output: (0, schema_1.validateFinalOutput)(bounded),
        architectural_boundaries_layer: boundaries,
    };
}
