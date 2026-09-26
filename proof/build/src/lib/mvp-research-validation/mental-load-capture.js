"use strict";
/**
 * Mental-load capture — every input should leave understanding, not “note created.”
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.composeMentalLoadCaptureLines = composeMentalLoadCaptureLines;
/**
 * Build calm understanding facets when the composer would otherwise only confirm storage.
 */
function composeMentalLoadCaptureLines(params) {
    const confirmation_suffix = "Held so you do not have to reconstruct it later.";
    let what_changed = params.whatChangedHeld;
    if (!what_changed) {
        if (params.isDocument) {
            what_changed =
                "Document held as evidence in the care reality — not only as a file summary.";
        }
        else if (params.observationCount <= 1) {
            what_changed =
                "First care observations are held — unknowns stay open until clearer evidence arrives.";
        }
        else if (params.hasPriorConnection) {
            what_changed =
                "This stays connected to what was already held — you do not have to reconstruct the thread.";
        }
        else {
            what_changed = "Something new is held alongside earlier notes.";
        }
    }
    const connected_line = params.hasPriorConnection
        ? "Connected to what you already shared in this care situation."
        : null;
    const unknown_invite = params.openUnknowns.length > 0
        ? null
        : params.observationCount <= 2
            ? "If something important is still unclear, you can add it when you know."
            : null;
    return {
        confirmation_suffix,
        what_changed,
        connected_line,
        unknown_invite,
    };
}
