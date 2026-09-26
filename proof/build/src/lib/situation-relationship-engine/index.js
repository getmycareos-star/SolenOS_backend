"use strict";
/**
 * Situation Relationship Engine — sole authority for same/update/related/new
 * before CareEvents land on the CareContext spine.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.composeIdentityMismatchAsk = exports.looksLikeCareDecision = exports.answersOpenUncertaintyGap = exports.continuesUnderlyingIssue = exports.SITUATION_RELATIONSHIP_ENGINE_PURPOSE = exports.findReinforcementTargetObservation = exports.evaluateSituationRelationship = void 0;
var evaluate_1 = require("./evaluate");
Object.defineProperty(exports, "evaluateSituationRelationship", { enumerable: true, get: function () { return evaluate_1.evaluateSituationRelationship; } });
Object.defineProperty(exports, "findReinforcementTargetObservation", { enumerable: true, get: function () { return evaluate_1.findReinforcementTargetObservation; } });
Object.defineProperty(exports, "SITUATION_RELATIONSHIP_ENGINE_PURPOSE", { enumerable: true, get: function () { return evaluate_1.SITUATION_RELATIONSHIP_ENGINE_PURPOSE; } });
var signals_1 = require("./signals");
Object.defineProperty(exports, "continuesUnderlyingIssue", { enumerable: true, get: function () { return signals_1.continuesUnderlyingIssue; } });
Object.defineProperty(exports, "answersOpenUncertaintyGap", { enumerable: true, get: function () { return signals_1.answersOpenUncertaintyGap; } });
Object.defineProperty(exports, "looksLikeCareDecision", { enumerable: true, get: function () { return signals_1.looksLikeCareDecision; } });
Object.defineProperty(exports, "composeIdentityMismatchAsk", { enumerable: true, get: function () { return signals_1.composeIdentityMismatchAsk; } });
