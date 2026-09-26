"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.InputClassificationResultSchema = void 0;
exports.assertClassifierOutputBoundary = assertClassifierOutputBoundary;
const zod_1 = require("zod");
const contract_constants_1 = require("./contract-constants");
exports.InputClassificationResultSchema = zod_1.z
    .object({
    mode: zod_1.z.enum(contract_constants_1.INPUT_MODES),
    confidence: zod_1.z.number().min(0).max(1).optional(),
})
    .strict();
function assertClassifierOutputBoundary(output) {
    const allowed = new Set(["mode", "confidence"]);
    for (const key of Object.keys(output)) {
        if (!allowed.has(key)) {
            throw new Error(`classifier output drift — forbidden field: ${key}`);
        }
    }
}
