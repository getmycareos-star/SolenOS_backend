"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.questionsFromUnknowns = exports.clarificationTargetsFromUnknowns = exports.deriveExplicitUnknowns = exports.UNKNOWN_PRIORITIES = void 0;
/**
 * Back-compat re-export — Unknowns Engine lives in src/lib/unknowns-engine.
 * Dementia is a profile there, not architecture here.
 */
var unknowns_engine_1 = require("../unknowns-engine");
Object.defineProperty(exports, "UNKNOWN_PRIORITIES", { enumerable: true, get: function () { return unknowns_engine_1.UNKNOWN_PRIORITIES; } });
Object.defineProperty(exports, "deriveExplicitUnknowns", { enumerable: true, get: function () { return unknowns_engine_1.deriveExplicitUnknowns; } });
Object.defineProperty(exports, "clarificationTargetsFromUnknowns", { enumerable: true, get: function () { return unknowns_engine_1.clarificationTargetsFromUnknowns; } });
Object.defineProperty(exports, "questionsFromUnknowns", { enumerable: true, get: function () { return unknowns_engine_1.questionsFromUnknowns; } });
