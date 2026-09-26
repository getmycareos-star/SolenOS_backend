"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.scanTextForViolations = scanTextForViolations;
exports.remediateText = remediateText;
exports.scanAllSurfaces = scanAllSurfaces;
const contract_constants_1 = require("./contract-constants");
function scanTextForViolations(field, text) {
    const violations = [];
    for (const pattern of contract_constants_1.DIAGNOSIS_VIOLATION_PATTERNS) {
        const match = text.match(pattern);
        if (match) {
            violations.push({
                rule: "never_diagnose",
                field,
                matched_text: match[0],
                severity: "critical",
                remediation: "These observations may warrant medical evaluation.",
            });
        }
    }
    for (const pattern of contract_constants_1.INVENTED_CERTAINTY_PATTERNS) {
        const match = text.match(pattern);
        if (match) {
            violations.push({
                rule: "never_pretend_confidence",
                field,
                matched_text: match[0],
                severity: "high",
                remediation: "Express confidence proportional to available evidence.",
            });
        }
    }
    for (const pattern of contract_constants_1.ENGAGEMENT_VIOLATION_PATTERNS) {
        const match = text.match(pattern);
        if (match) {
            violations.push({
                rule: "never_optimize_for_engagement",
                field,
                matched_text: match[0],
                severity: "medium",
                remediation: "Remove engagement-driven language.",
            });
        }
    }
    return violations;
}
function remediateText(text) {
    let result = text;
    let remediated = false;
    for (const { pattern, replacement } of contract_constants_1.SAFE_ALTERNATIVES) {
        if (pattern.test(result)) {
            result = result.replace(pattern, replacement);
            remediated = true;
        }
    }
    return { text: result, remediated };
}
function scanAllSurfaces(surfaces) {
    const all = [];
    for (const [field, value] of Object.entries(surfaces)) {
        const texts = Array.isArray(value) ? value : [value];
        for (const t of texts) {
            if (typeof t === "string" && t.trim()) {
                all.push(...scanTextForViolations(field, t));
            }
        }
    }
    return all;
}
