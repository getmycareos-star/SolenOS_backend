"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DEMENTIA_UNKNOWNS_PROFILE = exports.DEFAULT_CLINICAL_PROFILE_ID = exports.CLINICAL_UNKNOWNS_PROFILES = void 0;
exports.getClinicalUnknownsProfile = getClinicalUnknownsProfile;
const dementia_1 = require("./dementia");
Object.defineProperty(exports, "DEMENTIA_UNKNOWNS_PROFILE", { enumerable: true, get: function () { return dementia_1.DEMENTIA_UNKNOWNS_PROFILE; } });
const clinical_profile_1 = require("../../clinical-profile");
/** Registry — add Parkinson's, stroke, etc. without changing the engine. */
exports.CLINICAL_UNKNOWNS_PROFILES = {
    dementia: dementia_1.DEMENTIA_UNKNOWNS_PROFILE,
};
/** Re-export single SoT — dementia is MVP default; engines stay disease-agnostic. */
exports.DEFAULT_CLINICAL_PROFILE_ID = clinical_profile_1.DEFAULT_CLINICAL_PROFILE_ID;
function getClinicalUnknownsProfile(profileId = exports.DEFAULT_CLINICAL_PROFILE_ID) {
    return exports.CLINICAL_UNKNOWNS_PROFILES[profileId] ?? dementia_1.DEMENTIA_UNKNOWNS_PROFILE;
}
