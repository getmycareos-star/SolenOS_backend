"use strict";
/** Retention Engine — return value loop: what changed while I was gone? */
Object.defineProperty(exports, "__esModule", { value: true });
exports.RETURN_DELTA_THRESHOLD_MS = exports.MAX_RETURN_ACTION_ITEMS = exports.RETENTION_RULES = exports.RETURN_STATE_SECTIONS = exports.RETENTION_ENGINE_DEFINING_PRINCIPLE = exports.RETENTION_ENGINE_IDENTITY = void 0;
exports.RETENTION_ENGINE_IDENTITY = "What changed while I was gone? must be the default entry experience.";
exports.RETENTION_ENGINE_DEFINING_PRINCIPLE = "Every absence creates information delta. That delta is the product.";
exports.RETURN_STATE_SECTIONS = [
    "what_changed_since_last_visit",
    "what_got_worse",
    "what_got_better",
    "what_needs_action_now",
    "what_is_stable",
];
exports.RETENTION_RULES = [
    "no_greetings_on_return",
    "no_welcome_back_chat",
    "no_empty_dashboard",
    "no_static_view_without_delta",
    "compute_not_store_summary",
    "max_three_action_items",
];
exports.MAX_RETURN_ACTION_ITEMS = 3;
/** Minimum inactivity before return delta is meaningful (ms) */
exports.RETURN_DELTA_THRESHOLD_MS = 60 * 60 * 1000;
