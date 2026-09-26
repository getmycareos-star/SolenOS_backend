"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.detectPerspectiveConflicts = detectPerspectiveConflicts;
const contract_constants_1 = require("./contract-constants");
function eventText(event) {
    const snippet = event.attributes.source_situation_text;
    const extra = typeof snippet === "string" ? snippet : "";
    return `${event.raw_input} ${extra}`;
}
function eventCaregiverId(event) {
    return event.source_attribution?.caregiver_id ?? "unknown";
}
function detectPerspectiveConflicts(events, asOf) {
    const conflicts = [];
    const active = events.filter((e) => e.status !== "invalidated" && e.status !== "superseded");
    for (const { type, ...patterns } of contract_constants_1.CONTRADICTION_PATTERNS) {
        const negativeKey = "less" in patterns ? "less" : "decline";
        const positiveKey = "more" in patterns ? "more" : "stable";
        const negativePattern = patterns[negativeKey];
        const positivePattern = patterns[positiveKey];
        const negativeEvents = active.filter((e) => negativePattern.test(eventText(e)));
        const positiveEvents = active.filter((e) => positivePattern.test(eventText(e)));
        if (negativeEvents.length === 0 || positiveEvents.length === 0)
            continue;
        const neg = negativeEvents[negativeEvents.length - 1];
        const pos = positiveEvents[positiveEvents.length - 1];
        const negCaregiver = eventCaregiverId(neg);
        const posCaregiver = eventCaregiverId(pos);
        if (negCaregiver === posCaregiver && neg.id === pos.id)
            continue;
        conflicts.push({
            conflict_id: `mc_${type}_${neg.id}_${pos.id}`,
            event_ids: [neg.id, pos.id],
            conflicting_sources: [...new Set([negCaregiver, posCaregiver])],
            contradiction_type: type,
            resolution_status: "preserved_both",
            description: `Internal: conflicting ${type} observations preserved — sources linked in audit trail`,
            shared_abstract_message: `${type.charAt(0).toUpperCase() + type.slice(1)} status: inconsistent reports`,
            recorded_at: asOf,
        });
    }
    return conflicts;
}
