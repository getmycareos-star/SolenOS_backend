"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.recordBoundaryAudit = recordBoundaryAudit;
exports.resetArchitecturalBoundariesStore = resetArchitecturalBoundariesStore;
const audits = [];
function recordBoundaryAudit(entry) {
    audits.push({ ...entry, captured_at: new Date().toISOString() });
    if (audits.length > 100)
        audits.shift();
}
function resetArchitecturalBoundariesStore() {
    audits.length = 0;
}
