"use strict";
/** Edge State Machine — operational state before interpretation. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.DEGRADED_MIN_EVENTS = exports.STALE_THRESHOLD_DAYS = exports.EDGE_STATE_RULES = exports.EDGE_STATE_CLASSIFICATION_ORDER = exports.EDGE_STATES = exports.EDGE_STATE_DEFINING_PRINCIPLE = exports.EDGE_STATE_IDENTITY = void 0;
exports.EDGE_STATE_IDENTITY = "SolenOS must always declare its operational state before producing interpretation.";
exports.EDGE_STATE_DEFINING_PRINCIPLE = "Edge states are not exceptions — they are the primary operating conditions of caregiving reality.";
exports.EDGE_STATES = [
    "crisis",
    "conflict",
    "stale",
    "degraded",
    "bootstrap",
    "normal",
];
/** Classification priority — first match wins (crisis highest). */
exports.EDGE_STATE_CLASSIFICATION_ORDER = [
    "crisis",
    "conflict",
    "stale",
    "bootstrap",
    "degraded",
    "normal",
];
exports.EDGE_STATE_RULES = [
    "exactly_one_edge_state_per_cycle",
    "state_declared_before_output",
    "per_state_engine_activation",
    "per_state_output_restrictions",
    "no_cross_state_behavior_leakage",
];
/** Days without events → stale mode threshold */
exports.STALE_THRESHOLD_DAYS = 7;
/** Minimum events before leaving degraded/bootstrap toward normal */
exports.DEGRADED_MIN_EVENTS = 3;
