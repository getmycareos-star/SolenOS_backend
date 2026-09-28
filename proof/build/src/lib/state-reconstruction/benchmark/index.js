"use strict";
/**
 * State Reconstruction — Benchmark Index
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.FAILURE_TAXONOMY = exports.BENCHMARK_REQUIREMENTS = exports.formatValidationReport = exports.validateReconstructionResult = exports.validateCareState = exports.printBenchmarkSummary = exports.runBenchmarkSuite = void 0;
exports.runFullBenchmark = runFullBenchmark;
var suite_1 = require("./suite");
Object.defineProperty(exports, "runBenchmarkSuite", { enumerable: true, get: function () { return suite_1.runBenchmarkSuite; } });
Object.defineProperty(exports, "printBenchmarkSummary", { enumerable: true, get: function () { return suite_1.printBenchmarkSummary; } });
var validators_1 = require("./validators");
Object.defineProperty(exports, "validateCareState", { enumerable: true, get: function () { return validators_1.validateCareState; } });
Object.defineProperty(exports, "validateReconstructionResult", { enumerable: true, get: function () { return validators_1.validateReconstructionResult; } });
Object.defineProperty(exports, "formatValidationReport", { enumerable: true, get: function () { return validators_1.formatValidationReport; } });
var contract_constants_1 = require("./contract-constants");
Object.defineProperty(exports, "BENCHMARK_REQUIREMENTS", { enumerable: true, get: function () { return contract_constants_1.BENCHMARK_REQUIREMENTS; } });
Object.defineProperty(exports, "FAILURE_TAXONOMY", { enumerable: true, get: function () { return contract_constants_1.FAILURE_TAXONOMY; } });
/**
 * Run full benchmark and validation
 */
async function runFullBenchmark() {
    const { runBenchmarkSuite, printBenchmarkSummary } = await Promise.resolve().then(() => __importStar(require("./suite")));
    const { validateReconstructionResult } = await Promise.resolve().then(() => __importStar(require("./validators")));
    const { reconstructCareState } = await Promise.resolve().then(() => __importStar(require("../engine")));
    // Run benchmarks
    const results = await runBenchmarkSuite();
    printBenchmarkSummary(results);
    // Run validation on last result (example)
    if (results.length > 0) {
        // In real usage, you'd validate each result
        console.log("Validation would be run on each reconstruction result");
    }
}
