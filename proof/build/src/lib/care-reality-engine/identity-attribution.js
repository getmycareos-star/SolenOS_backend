"use strict";
/**
 * Phase 1 — Care Recipient Identity + Contributor attribution.
 * Never assume kinship words name a specific person. Never auto-merge people.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.noteMentionsUnboundKinshipLabel = noteMentionsUnboundKinshipLabel;
exports.resolveIdentityAttribution = resolveIdentityAttribution;
exports.ensureCareRecipientNamed = ensureCareRecipientNamed;
const care_recipient_identity_1 = require("../care-recipient-identity");
/**
 * Kinship / role words in notes are not medical identity.
 * Detection only — never write them into CareRecipient.displayName.
 */
function noteMentionsUnboundKinshipLabel(rawText) {
    return /\b(mom|mum|mother|dad|father|wife|husband|partner|grandma|grandpa|grandmother|grandfather)\b/i.test(rawText);
}
function resolveIdentityAttribution(params) {
    const now = params.nowIso ?? new Date().toISOString();
    const existing = (0, care_recipient_identity_1.getCareRecipientIdentity)(params.careRecipientId);
    const displayName = (0, care_recipient_identity_1.getCareRecipientDisplayName)(params.careRecipientId);
    const care_recipient = {
        id: params.careRecipientId,
        displayName,
        relationship: existing?.relationship ?? null,
        createdAt: existing?.updated_at ?? now,
        createdBy: params.contributorId,
    };
    const contributor = {
        id: params.contributorId,
        name: params.contributorName ?? null,
        relationship: null,
        role: "contributor",
    };
    const needs_recipient_clarification = !displayName &&
        (!!params.rawText?.trim() && noteMentionsUnboundKinshipLabel(params.rawText));
    return {
        care_recipient,
        contributor,
        needs_recipient_clarification,
        attribution_ready: true,
    };
}
function ensureCareRecipientNamed(params) {
    return (0, care_recipient_identity_1.setCareRecipientDisplayName)({
        careKey: params.careRecipientId,
        displayName: params.displayName,
        relationship: params.relationship,
    });
}
