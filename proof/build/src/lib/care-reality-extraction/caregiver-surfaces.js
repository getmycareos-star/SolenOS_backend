"use strict";
/**
 * Caregiver-facing surfaces from partitioned extraction — never human_fact ≈ raw.
 * Locked A: display kinship for orientation only — never durable identity writes here.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.caregiverFacingLinesFromExtraction = caregiverFacingLinesFromExtraction;
exports.caregiverFacingLinesFromCaptureText = caregiverFacingLinesFromCaptureText;
exports.applySessionKinshipDisplay = applySessionKinshipDisplay;
const output_quality_1 = require("../output-quality");
const extract_1 = require("./extract");
function normalizeSurfaceLine(raw) {
    const line = raw.replace(/\s+/g, " ").trim();
    if (!line)
        return "";
    return line.endsWith(".") ? line : `${line}.`;
}
/**
 * True when an extraction line is still essentially the whole capture (echo),
 * not a short partitioned observation/event/action facet.
 * Short notes may equal their extraction line — that is allowed.
 */
function isFullCaptureEcho(line, latestRawText) {
    const f = line.replace(/\s+/g, " ").trim().replace(/\.$/, "");
    const raw = (latestRawText ?? "").replace(/\s+/g, " ").trim();
    if (!f)
        return true;
    if ((0, output_quality_1.looksLikeRawNoteDump)(f, latestRawText))
        return true;
    if (!raw)
        return f.length >= 90;
    // Short capture: partitioned line may equal the note — keep it.
    if (raw.length < 48)
        return f.length >= 55;
    // Reject near-whole paste of a long capture
    if (f.length >= Math.max(48, raw.length - 12))
        return true;
    if (f.length >= 40 &&
        raw.toLowerCase().includes(f.toLowerCase()) &&
        f.length >= raw.length * 0.75) {
        return true;
    }
    return false;
}
/**
 * Prefer already-partitioned observation / event / action lines for caregiver UI.
 * Drops full-capture echo; keeps short partitioned facets (even when they match a short note).
 */
function caregiverFacingLinesFromExtraction(params) {
    const extraction = params.extraction;
    if (!extraction)
        return [];
    const max = params.max ?? 4;
    const includeEvents = params.includeEvents !== false;
    const includeActions = params.includeActions !== false;
    const out = [];
    const push = (raw) => {
        if (!raw?.trim())
            return;
        if (isFullCaptureEcho(raw, params.latestRawText))
            return;
        // Long captures: drop verbatim slices — caregivers need understanding, not note paste.
        if ((0, output_quality_1.isNearRawCaregiverFacet)(raw, params.latestRawText))
            return;
        const line = normalizeSurfaceLine(raw);
        if (line.length < 8 || line.length > 120)
            return;
        if (out.some((l) => l.toLowerCase() === line.toLowerCase()))
            return;
        out.push(line);
    };
    for (const focus of extraction.observation_focus_lines ?? [])
        push(focus);
    for (const o of extraction.observations ?? [])
        push(o.description);
    if (includeEvents) {
        for (const e of extraction.events ?? [])
            push(e.description);
    }
    if (includeActions) {
        for (const a of extraction.actions ?? [])
            push(a.description);
    }
    return out.slice(0, max);
}
/**
 * Extract + surface lines from a single capture — used for prior connection facts
 * and focus when ACS only holds human_fact/raw.
 */
function caregiverFacingLinesFromCaptureText(params) {
    const raw = params.rawText?.trim() ?? "";
    if (raw.length < 8)
        return [];
    const extraction = (0, extract_1.extractCareRealityFromText)({ rawText: raw });
    return caregiverFacingLinesFromExtraction({
        extraction,
        latestRawText: raw,
        max: params.max ?? 2,
        includeEvents: params.includeEvents,
        includeActions: params.includeActions,
    });
}
/**
 * Session kinship display rewrite only — never durable identity write (Locked A).
 * Orientation: Mom/Dad label when ACS subject already holds it.
 */
function applySessionKinshipDisplay(line, subjectLabel) {
    const subject = subjectLabel?.trim() ?? "";
    if (!subject || subject === "they" || subject === "Your loved one") {
        return line;
    }
    return line
        .replace(/\bmy\s+(dad|father)\b/gi, () => /^(dad|father)$/i.test(subject) ? subject : "Dad")
        .replace(/\bmy\s+(mom|mum|mother)\b/gi, () => /^(mom|mum|mother)$/i.test(subject) ? subject : "Mom");
}
