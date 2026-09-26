"use strict";
/** UI Runtime — situation-centric operational surface (not chat / dashboard). */
Object.defineProperty(exports, "__esModule", { value: true });
exports.ACTIVE_SITUATION_STORAGE_KEY = exports.SITUATIONS_STORAGE_KEY = exports.TIMELINE_STORAGE_KEY = exports.UI_RUNTIME_DESIGN_PRINCIPLE = exports.FORBIDDEN_UI_PATTERNS = exports.FEEDBACK_CORRECTION_KINDS = exports.CAREGIVER_SIDEBAR_SECTION_LABELS = exports.SIDEBAR_SECTION_LABELS = exports.OPS_SIDEBAR_SECTION_IDS = exports.CAREGIVER_SIDEBAR_SECTION_IDS = exports.SIDEBAR_SECTION_IDS = exports.DOCUMENT_SOURCE_TYPES = exports.TIMELINE_ENTRY_TYPES = exports.SITUATION_STATUSES = exports.DECISION_RISK_LEVELS = exports.UI_EVENT_LOOP_STAGES = exports.UI_RUNTIME_ONE_LINE_TRUTH = exports.UI_RUNTIME_IDENTITY = void 0;
exports.UI_RUNTIME_IDENTITY = "solenos — the trusted place where a person's care journey lives";
exports.UI_RUNTIME_ONE_LINE_TRUTH = "User Input → Context → Memory → Time → Priority → Conflict → Action → Safety → Output Assembly → Replace Decision Card → Append Timeline → Update Situation";
exports.UI_EVENT_LOOP_STAGES = [
    "user_input",
    "context",
    "memory",
    "time",
    "priority",
    "conflict",
    "action",
    "safety",
    "output_assembly",
    "replace_decision_card",
    "append_timeline",
    "update_situation",
];
exports.DECISION_RISK_LEVELS = ["LOW", "MEDIUM", "HIGH"];
exports.SITUATION_STATUSES = ["active", "blocked", "waiting", "resolved"];
exports.TIMELINE_ENTRY_TYPES = [
    "decision",
    "document",
    "correction",
    "system_event",
    "demand_completed",
];
exports.DOCUMENT_SOURCE_TYPES = ["medical", "insurance", "benefits", "other"];
exports.SIDEBAR_SECTION_IDS = [
    "active_situations",
    "observations",
    "care_profile",
    "care_context",
    "timeline",
    "memory",
    "documents",
    "responsibility_graph",
    "safety_settings",
    "system_settings",
    "feedback_corrections",
    "system_health",
    "about_solenos",
];
/** Caregiver-facing nav — Living Care Record + continuity only (not ops console). */
exports.CAREGIVER_SIDEBAR_SECTION_IDS = [
    "active_situations",
    "timeline",
    "about_solenos",
];
/** Ops / instrumentation sections — gated behind OPS_SECRET. */
exports.OPS_SIDEBAR_SECTION_IDS = [
    "observations",
    "care_profile",
    "care_context",
    "memory",
    "documents",
    "responsibility_graph",
    "safety_settings",
    "system_settings",
    "feedback_corrections",
    "system_health",
];
exports.SIDEBAR_SECTION_LABELS = {
    active_situations: "Active Situations",
    observations: "Observations",
    care_profile: "Care Profile",
    care_context: "Care Context",
    timeline: "Timeline",
    memory: "Memory",
    documents: "Documents",
    responsibility_graph: "Responsibility Graph",
    safety_settings: "Safety Settings",
    system_settings: "System Settings",
    feedback_corrections: "Feedback & Corrections",
    system_health: "System Health",
    about_solenos: "About SolenOS",
};
/** Plain-language caregiver labels (ops keeps SIDEBAR_SECTION_LABELS). */
exports.CAREGIVER_SIDEBAR_SECTION_LABELS = {
    active_situations: "Open situations",
    timeline: "Care timeline",
    about_solenos: "About SolenOS",
};
exports.FEEDBACK_CORRECTION_KINDS = [
    "incorrect_assumption",
    "outdated_context",
    "bad_recommendation",
    "missing_information",
];
exports.FORBIDDEN_UI_PATTERNS = [
    "chat bubbles",
    "conversation threads",
    "assistant personas",
    "infinite feeds",
    "dashboards",
    "KPI screens",
    "gamification",
    "engagement loops",
];
exports.UI_RUNTIME_DESIGN_PRINCIPLE = "Caregiver chrome is Living Care Record continuity — ops instrumentation stays behind an ops gate";
exports.TIMELINE_STORAGE_KEY = "solenos_ui_timeline_v1";
exports.SITUATIONS_STORAGE_KEY = "solenos_ui_situations_v1";
exports.ACTIVE_SITUATION_STORAGE_KEY = "solenos_ui_active_situation_id";
