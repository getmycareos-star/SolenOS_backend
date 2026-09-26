"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.inferSourceType = inferSourceType;
exports.attachSourceAttribution = attachSourceAttribution;
exports.attachAttributionToEvents = attachAttributionToEvents;
exports.ensureEventHasAttribution = ensureEventHasAttribution;
function inferSourceType(event) {
    if (event.status === "provisional" || event.status === "unparsed_raw") {
        return "inferred";
    }
    if (event.source === "document") {
        return "reported";
    }
    if (event.integrity.sources?.includes("user_correction")) {
        return "direct_observation";
    }
    return "direct_observation";
}
function attachSourceAttribution(event, caregiverId, careRecipientId, sourceType) {
    const attribution = {
        caregiver_id: caregiverId,
        care_recipient_id: careRecipientId,
        source_type: sourceType ?? inferSourceType(event),
        observed_at: event.event_time.start ?? event.ingestion_time,
        ingestion_context: typeof event.attributes.source_situation_text === "string"
            ? event.attributes.source_situation_text.slice(0, 200)
            : null,
    };
    return { ...event, source_attribution: attribution };
}
function attachAttributionToEvents(events, caregiverId, careRecipientId) {
    return events.map((e) => attachSourceAttribution(e, caregiverId, careRecipientId));
}
function ensureEventHasAttribution(event, fallbackCaregiverId, fallbackRecipientId) {
    if (event.source_attribution)
        return event;
    return attachSourceAttribution(event, fallbackCaregiverId, fallbackRecipientId);
}
