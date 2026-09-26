"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.isSolenOSLanguage = isSolenOSLanguage;
exports.coerceSolenOSLanguage = coerceSolenOSLanguage;
const constants_1 = require("./constants");
function isSolenOSLanguage(value) {
    return (typeof value === "string" &&
        constants_1.SOLENOS_LANGUAGES.includes(value));
}
function coerceSolenOSLanguage(value) {
    return isSolenOSLanguage(value) ? value : constants_1.DEFAULT_SOLENOS_LANGUAGE;
}
