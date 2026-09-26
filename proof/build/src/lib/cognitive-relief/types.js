"use strict";
/** Cognitive Relief Modules — types separate from prioritization engine. */
Object.defineProperty(exports, "__esModule", { value: true });
exports.DEFAULT_PROFILE = exports.EVENT_LOG_CATEGORIES = exports.CHECKIN_PERIODS = exports.SUMMARY_AUDIENCES = void 0;
exports.SUMMARY_AUDIENCES = [
    "new_doctor",
    "family_member",
    "aide",
    "custom",
];
exports.CHECKIN_PERIODS = ["daily", "weekly"];
exports.EVENT_LOG_CATEGORIES = ["symptom", "incident", "decision"];
exports.DEFAULT_PROFILE = {
    care_recipient_basics: "",
    known_conditions: [],
    current_medications: [],
    key_dates: [],
    care_team: [],
    tagged_event_log: [],
    location_index: [],
};
