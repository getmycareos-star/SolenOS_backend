"use strict";
/** Event-Sourced Data Storage Architecture — four-layer persistence. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.EVENT_SOURCED_STORAGE_RULES = exports.PROJECTION_STORE_RULES = exports.EVENT_STORE_RULES = exports.STORAGE_LAYERS = exports.EVENT_SOURCED_STORAGE_DEFINING_PRINCIPLE = exports.EVENT_SOURCED_STORAGE_IDENTITY = void 0;
exports.EVENT_SOURCED_STORAGE_IDENTITY = "SolenOS does not store care data. It stores the evolution of care reality over time.";
exports.EVENT_SOURCED_STORAGE_DEFINING_PRINCIPLE = "Nothing in SolenOS is stored as truth. Everything is stored as an event that contributes to truth.";
exports.STORAGE_LAYERS = [
    "event_store",
    "projection_store",
    "session_store",
    "derived_tables",
];
exports.EVENT_STORE_RULES = [
    "append_only",
    "immutable",
    "never_updated",
    "never_deleted_except_legal",
    "time_ordered",
];
exports.PROJECTION_STORE_RULES = [
    "not_manually_edited",
    "always_rebuilt_from_event_store",
    "cached_for_performance_only",
    "care_context_is_computed_view",
];
exports.EVENT_SOURCED_STORAGE_RULES = [
    "all_state_reconstructable_from_events",
    "care_context_is_projection",
    "forbid_direct_state_mutation",
    "derived_tables_are_disposable",
    "event_sourcing_not_deferred",
];
