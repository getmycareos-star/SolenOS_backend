"use strict";
/** Continuous execution loop — the runtime engine connecting input to output. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.MAX_SURFACED_PRIORITY_ITEMS = exports.MAX_DIFF_SUMMARY_LINES = exports.EXECUTION_LOOP_DEFINITION = exports.SYSTEM_MODES = exports.UNCERTAINTY_STATES = exports.STATE_UPDATE_OPERATIONS = exports.UNIFIED_INPUT_TYPES = exports.EXECUTION_LOOP_PHASES = exports.CONTINUOUS_EXECUTION_IDENTITY = void 0;
exports.CONTINUOUS_EXECUTION_IDENTITY = "SolenOS runs a single persistent loop: INPUT → PARSE → NORMALIZE → UPDATE STATE → DIFF → GENERATE OUTPUT → WAIT";
exports.EXECUTION_LOOP_PHASES = [
    "input",
    "parse",
    "normalize",
    "update_state",
    "diff",
    "generate_output",
    "wait",
];
exports.UNIFIED_INPUT_TYPES = [
    "situation",
    "document",
    "correction",
    "follow_up_answer",
    "observation",
    "update",
    "idle_refresh",
];
exports.STATE_UPDATE_OPERATIONS = ["add", "correct", "link"];
exports.UNCERTAINTY_STATES = ["OPEN", "ASKED", "ANSWERED", "INVALIDATED"];
exports.SYSTEM_MODES = ["empty", "bootstrap", "continuous"];
exports.EXECUTION_LOOP_DEFINITION = "An event-sourced system that continuously transforms unstructured real-world inputs into a versioned continuity state through a deterministic execution loop governed by diff-based state updates and explicit uncertainty management.";
exports.MAX_DIFF_SUMMARY_LINES = 12;
exports.MAX_SURFACED_PRIORITY_ITEMS = 8;
