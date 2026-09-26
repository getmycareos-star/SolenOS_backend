"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.withDecisionTrace = void 0;
exports.withMeta = withMeta;
/** @deprecated Schema is strict 6-field — pass full payload directly. */
function withMeta(fields) {
    return fields;
}
/** @deprecated */
exports.withDecisionTrace = withMeta;
