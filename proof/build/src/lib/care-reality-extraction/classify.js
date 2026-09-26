"use strict";
/**
 * Principle-based fragment classification — discourse structure, not scenario nouns.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.looksLikeIntentionNotEvent = exports.looksLikeCareJourneyEventFragment = void 0;
exports.looksLikeContributorLoadFragment = looksLikeContributorLoadFragment;
exports.looksLikeDisagreementPerspectiveFragment = looksLikeDisagreementPerspectiveFragment;
exports.looksLikeCareDecisionFragment = looksLikeCareDecisionFragment;
exports.looksLikeOpenUnknownFragment = looksLikeOpenUnknownFragment;
exports.classifyExtractionFragment = classifyExtractionFragment;
const care_reality_output_1 = require("../care-reality-output");
const outcomes_1 = require("./outcomes");
const events_1 = require("./events");
const actions_1 = require("./actions");
/** Contributor cognitive load / meta — not care-recipient observation. */
function looksLikeContributorLoadFragment(text) {
    const t = text.trim();
    if (!t)
        return false;
    if ((0, care_reality_output_1.looksLikeCaregiverExperienceOnly)(t))
        return true;
    if (/\bi don'?t (?:even )?know what matters\b/i.test(t))
        return true;
    if (/\bdon'?t (?:even )?know where to start\b/i.test(t))
        return true;
    if (/\btrying to keep track of\b/i.test(t))
        return true;
    if (/\bevery time i think i'?ve figured\b/i.test(t))
        return true;
    if (/\bfeels like every time\b/i.test(t) && /\b(?:appointment|medication|changing)\b/i.test(t)) {
        return true;
    }
    if (/\bi'?m (?:just )?trying to (?:keep|manage|juggle)\b/i.test(t))
        return true;
    return false;
}
/**
 * Another person disputes caregiver concern level — perspective, not recipient observation.
 * Structural: third party + worry/overreact framing — not a named-family vocabulary.
 */
function looksLikeDisagreementPerspectiveFragment(text) {
    const t = text.trim();
    if (!t)
        return false;
    if (/\b(?:thinks?|said|says|telling me)\b/i.test(t) &&
        /\b(?:worry(?:ing)?|overreact(?:ing)?|too much|overly concern)\b/i.test(t)) {
        return true;
    }
    if (/\bnot here every day\b/i.test(t) && /\b(?:think|worry|too much)\b/i.test(t)) {
        return true;
    }
    return false;
}
var events_2 = require("./events");
Object.defineProperty(exports, "looksLikeCareJourneyEventFragment", { enumerable: true, get: function () { return events_2.looksLikeCareJourneyEventFragment; } });
Object.defineProperty(exports, "looksLikeIntentionNotEvent", { enumerable: true, get: function () { return events_2.looksLikeIntentionNotEvent; } });
/** Deliberate care choice — Decision, not Event or Observation. */
function looksLikeCareDecisionFragment(text) {
    const t = text.trim();
    if (!t)
        return false;
    // Recommendations are not decisions until accepted or acted on (past action, not infinitive "changing")
    if (/\b(?:should|could|might want to|recommend(?:ed|s)?|suggest(?:ed|s)?|advised?)\b/i.test(t) &&
        !/\b(?:decid(?:ed|e)|chose|chosen|chang(?:ed)|switch(?:ed)|start(?:ed)|stopp(?:ed)|we (?:did|went)|they (?:did|chang(?:ed))|accepted|went with|opted)\b/i.test(t)) {
        return false;
    }
    if (/\b(?:medications?|medicine|meds?|dose|prescription)\b/i.test(t) &&
        /\b(?:chang(?:ed)|switch(?:ed)|start(?:ed)|stopp(?:ed)|adjust(?:ed)|review(?:ed))\b/i.test(t)) {
        return true;
    }
    // "they changed" / "we changed" without requiring past-only when no recommend language
    if (/\b(?:medications?|medicine|meds?|dose|prescription)\b/i.test(t) &&
        /\b(?:chang(?:ed|e)|switch(?:ed)?|start(?:ed)?|stopp(?:ed)?|adjust(?:ed)?)\b/i.test(t) &&
        !/\b(?:recommend|suggest|should|could|might want)\b/i.test(t)) {
        return true;
    }
    if (/\b(?:decid(?:ed|e)|chose|chosen|went with|opted)\b/i.test(t))
        return true;
    if (/\btold us to\b/i.test(t) && /\b(?:follow\s*up|primary|doctor|clinic)\b/i.test(t)) {
        return true;
    }
    return false;
}
/** Explicit open uncertainty — Unknown, not fact observation. */
function looksLikeOpenUnknownFragment(text) {
    const t = text.trim();
    if (!t)
        return false;
    if (/\bi(?:'m| am)? not sure (?:if|whether|why)\b/i.test(t))
        return true;
    if (/\bnot sure (?:if|whether)\b/i.test(t))
        return true;
    if (/\bcan'?t remember why\b/i.test(t))
        return true;
    if (/\bi honestly can'?t remember\b/i.test(t))
        return true;
    if (/\bdon'?t know (?:if|whether|why)\b/i.test(t) && t.length < 220)
        return true;
    if (/\b(?:unsure|uncertain)\b/i.test(t) && /\b(?:if|whether)\b/i.test(t))
        return true;
    if (/\brequires confirmation\b/i.test(t))
        return true;
    return false;
}
/** Recipient-anchored state/behavior discourse (structural pronouns / kinship display — not scenario lists). */
function looksLikeRecipientStateFragment(text) {
    const t = text.trim();
    if (!t)
        return false;
    if (/\b(?:she|he|they|mom|dad|mother|father|grandma|grandpa)\b/i.test(t) &&
        /\b(?:more|less|been|was|is|seemed|asked|tried|refus|eat|sleep|confused|upset|walk|left|leave)\b/i.test(t)) {
        return true;
    }
    if (/\b(?:noticed|saw|heard|observed)\b/i.test(t))
        return true;
    return false;
}
/**
 * Classify one fragment.
 * Order: load → disagreement → unknown(alt) → decision → action → event → outcome → unknown → observation → skip.
 */
function classifyExtractionFragment(text) {
    const t = text.trim();
    if (!t || t.length < 8)
        return "skip";
    if (looksLikeContributorLoadFragment(t))
        return "contributor_load";
    if (looksLikeDisagreementPerspectiveFragment(t))
        return "disagreement_perspective";
    if (looksLikeOpenUnknownFragment(t) &&
        /\b(?:or if|or whether|or (?:she|he|they)|not sure if)\b/i.test(t)) {
        return "unknown";
    }
    if (looksLikeCareDecisionFragment(t))
        return "decision";
    // Recommendations without acted choice are not decisions (and not observations)
    if (/\b(?:recommend(?:ed|s)?|suggest(?:ed|s)?|should|might want to)\b/i.test(t) &&
        !/\b(?:decid(?:ed)|chose|chang(?:ed)|switch(?:ed)|start(?:ed)|stopp(?:ed)|accepted|went with)\b/i.test(t)) {
        return "skip";
    }
    // Planned actions are not events (and not observations of what already happened).
    if ((0, events_1.looksLikeIntentionNotEvent)(t))
        return "skip";
    // Care journey moments before generic Action — doctor visit is Event, not a single blob Action.
    if ((0, events_1.looksLikeCareJourneyEventFragment)(t))
        return "event";
    // Deeds someone already performed — Action (not Decision, not Outcome).
    if ((0, actions_1.looksLikeCareActionFragment)(t))
        return "action";
    // Intentions are not outcomes.
    if ((0, outcomes_1.looksLikeIntentionNotOutcome)(t))
        return "skip";
    if ((0, outcomes_1.looksLikeOutcomeFragment)(t))
        return "outcome";
    if (looksLikeOpenUnknownFragment(t) && !looksLikeRecipientStateFragment(t.split(/[,.]/)[0] ?? t)) {
        return "unknown";
    }
    if (looksLikeOpenUnknownFragment(t))
        return "unknown";
    if (looksLikeRecipientStateFragment(t))
        return "observation";
    // Care-document / instruction conflict — observation without requiring kinship pronouns.
    if (/\b(?:medication|medicine|meds?|prescription|instructions?|med list|list)\b/i.test(t) &&
        /\b(?:don'?t match|does not match|do not match|mismatch|conflict|contradict|differs?|different from)\b/i.test(t)) {
        return "observation";
    }
    if (t.length >= 40 &&
        !looksLikeContributorLoadFragment(t) &&
        !looksLikeDisagreementPerspectiveFragment(t)) {
        if (/\b(?:she|he|they|mom|dad|her|his|their)\b/i.test(t))
            return "observation";
        // Care journey evidence without named pronouns (hospital stay, instructions, lists).
        if (/\b(?:hospital|clinic|medication|medicine|meds?|instructions?|discharge)\b/i.test(t)) {
            return "observation";
        }
    }
    return "skip";
}
