"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.withDecisionTrace = exports.withMeta = exports.SolenOSSchema = exports.SolenOSResponseSchema = exports.SOLENOS_FIELD_ORDER = void 0;
exports.validateAIResponse = validateAIResponse;
exports.isValidationError = isValidationError;
exports.extractSolenOSDisplayFields = extractSolenOSDisplayFields;
exports.extractSolenOSPayload = extractSolenOSPayload;
exports.gateForUI = gateForUI;
const types_1 = require("../consistency-determinism/types");
Object.defineProperty(exports, "SOLENOS_FIELD_ORDER", { enumerable: true, get: function () { return types_1.SOLENOS_FIELD_ORDER; } });
const final_output_contract_1 = require("../final-output-contract");
/** Canonical SolenOS output — identical to FinalOutputContract (source of truth). */
exports.SolenOSResponseSchema = final_output_contract_1.FinalOutputContractSchema;
exports.SolenOSSchema = exports.SolenOSResponseSchema;
function validateAIResponse(output) {
    try {
        return (0, final_output_contract_1.validateFinalOutput)((0, final_output_contract_1.extractFinalOutputPayload)(output));
    }
    catch (err) {
        if (typeof err === "object" &&
            err !== null &&
            err.type === "INVALID_FINAL_OUTPUT") {
            throw {
                ...err,
                type: "INVALID_SCHEMA",
            };
        }
        throw err;
    }
}
function isValidationError(error) {
    return (typeof error === "object" &&
        error !== null &&
        (error.type === "INVALID_SCHEMA" ||
            error.type === "INVALID_FINAL_OUTPUT") &&
        typeof error.message === "string" &&
        "raw_output" in error);
}
function extractSolenOSDisplayFields(output) {
    return output;
}
function extractSolenOSPayload(output) {
    return (0, final_output_contract_1.extractFinalOutputPayload)(output);
}
function gateForUI(output) {
    return validateAIResponse(output);
}
var fixtures_1 = require("./fixtures");
Object.defineProperty(exports, "withMeta", { enumerable: true, get: function () { return fixtures_1.withMeta; } });
Object.defineProperty(exports, "withDecisionTrace", { enumerable: true, get: function () { return fixtures_1.withDecisionTrace; } });
