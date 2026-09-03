/**
 * Functional Independence — 6-level model with ADL/IADL tracking.
 *
 * Critical distinctions preserved structurally:
 *   prompting ≠ supervision ≠ assistance ≠ dependence
 *
 * The independence model is the most defensible dementia-specific value:
 *   - It captures what the system CAN observe (level of independence).
 *   - It explicitly does NOT capture cause.
 *   - Unknown is a first-class state; "no report" ≠ independent.
 *
 * Functional changes are baseline→current with care-relevance only if
 * the change crosses a level boundary AND a baseline is known.
 */

import {
  INDEPENDENCE_ORDER,
  IndependenceLevelSchema,
  type FunctionalChange,
  type FunctionalObservation,
  type FunctionalObservationType,
  type IndependenceLevel,
  type Provenance,
} from "./types";
import { isIndependenceDecline } from "./types";

// ─── Activity classification ─────────────────────────────────────────────

export const ADL_ACTIVITIES: readonly FunctionalObservationType[] = [
  "bathing",
  "dressing",
  "toileting",
  "eating",
  "mobility",
  "grooming",
] as const;

export const IADL_ACTIVITIES: readonly FunctionalObservationType[] = [
  "medication_self_management",
  "finances",
  "transportation",
  "cooking",
  "shopping",
  "appointments",
  "communication_device",
  "household_management",
] as const;

export type ADLCategory = "adl" | "iadl" | "unknown";

export function classifyActivity(
  activity: FunctionalObservationType,
): ADLCategory {
  if ((ADL_ACTIVITIES as readonly FunctionalObservationType[]).includes(activity)) return "adl";
  if ((IADL_ACTIVITIES as readonly FunctionalObservationType[]).includes(activity)) return "iadl";
  return "unknown";
}

// ─── Independence Linguistic Patterns ─────────────────────────────────────

/**
 * Map of linguistic cue → independence level. These are CUES, not absolute
 * deciders. A single cue from a vague concern yields a low-confidence
 * level; a quantified observation yields high confidence.
 */
export const INDEPENDENCE_CUES: ReadonlyArray<{
  pattern: RegExp;
  level: IndependenceLevel;
  strength: "vague" | "specific" | "quantified";
}> = [
  // Prompting
  { pattern: /\b(?:needs? (?:a |some )?(?:reminder|prompt|nudge))\b/i, level: "needs_prompting", strength: "specific" },
  { pattern: /\b(?:I|we|daughter|son|caregiver) (?:have to|need to) (?:remind|prompt)\b/i, level: "needs_prompting", strength: "specific" },
  { pattern: /\b(?:she|he|they) (?:now )?needs? prompting\b/i, level: "needs_prompting", strength: "specific" },

  // Supervision
  { pattern: /\b(?:needs? (?:supervision|to be watched|watching over))\b/i, level: "needs_supervision", strength: "specific" },
  { pattern: /\b(?:I|we) (?:have to|need to) (?:watch|supervise|keep an eye|check on)\b/i, level: "needs_supervision", strength: "specific" },

  // Assistance (partial physical help)
  { pattern: /\b(?:needs? (?:help|assistance) (?:with|getting))\b/i, level: "needs_assistance", strength: "specific" },
  { pattern: /\b(?:I|we) (?:have to|need to) (?:help|assist)\b/i, level: "needs_assistance", strength: "specific" },
  { pattern: /\b(?:I|we) (?:help|assist) (?:her|him|them)\b/i, level: "needs_assistance", strength: "specific" },
  { pattern: /\b(?:some help|a little help|partial help)\b/i, level: "needs_assistance", strength: "specific" },

  // Dependence (full takeover)
  { pattern: /\b(?:I|we|daughter|son|caregiver) (?:do|administer|give|prepare|set up|manage) all\b.*\b(?:medications?|meds?|bathing|dressing|cooking)\b/i, level: "dependent", strength: "specific" },
  { pattern: /\b(?:I|we|daughter|son|caregiver) (?:do|administer|give|prepare|set up|manage) (?:her|his|their) (?:medications?|meds?|bathing|dressing|cooking)\b/i, level: "dependent", strength: "specific" },
  { pattern: /\badminister all (?:her|his|their) (?:medications?|meds?)\b/i, level: "dependent", strength: "specific" },
  { pattern: /\b(?:fills|fill|administers?|gives?|prepares?|sets? up) (?:her|his|their) (?:medications?|pills?)\b/i, level: "dependent", strength: "specific" },
  { pattern: /\b(?:her|his|their) (?:daughter|son|caregiver|wife|husband) (?:fills|administers|gives|prepares|sets up) (?:her|his|their|all) (?:medications?|meds?|pills?)\b/i, level: "dependent", strength: "specific" },
  { pattern: /\b(?:fully dependent|completely dependent|can'?t do (?:it|anything) (?:alone|on her own|on his own|by herself|by himself))\b/i, level: "dependent", strength: "specific" },
  { pattern: /\b(?:no longer (?:able to|can) (?:do|manage|handle|cook|dress|bath))\b/i, level: "dependent", strength: "specific" },
  { pattern: /\b(?:totally|completely)\s+(?:dependent|helpless)\b/i, level: "dependent", strength: "quantified" },

  // Independent (negative cues / explicit)
  { pattern: /\b(?:still|now|by) (?:her|him|them)self\b/i, level: "independent", strength: "specific" },
  { pattern: /\b(?:on her own|on his own|on their own|by herself|by himself|by themselves)\b/i, level: "independent", strength: "specific" },
  { pattern: /\bmanag(?:es?|ing) (?:her|his|their|on her own|on his own)\b/i, level: "independent", strength: "specific" },

  // Vague decline
  { pattern: /\b(?:getting worse|not as good|struggling|trouble with)\b/i, level: "needs_prompting", strength: "vague" },
  { pattern: /\b(?:used to (?:do|be able to|could))\b/i, level: "unknown", strength: "vague" },
] as const;

export type IndependenceClassification = {
  level: IndependenceLevel;
  strength: "vague" | "specific" | "quantified";
  matched: boolean;
};

/**
 * Classify a free-text functional cue into an independence level. Returns
 * `unknown` if no cue matches. The `strength` field tracks whether the
 * cue is vague, specific, or quantified.
 */
export function classifyIndependenceFromText(text: string): IndependenceClassification {
  for (const cue of INDEPENDENCE_CUES) {
    if (cue.pattern.test(text)) {
      return { level: cue.level, strength: cue.strength, matched: true };
    }
  }
  return { level: "unknown", strength: "vague", matched: false };
}

// ─── Functional Change ────────────────────────────────────────────────────

export type BuildFunctionalChangeInput = {
  change_id: string;
  subject_id: string;
  activity: FunctionalObservationType;
  baseline: {
    independence: IndependenceLevel;
    baseline_time: string | null;
    baseline_provenance: Provenance | null;
  };
  current: FunctionalObservation;
};

/**
 * Build a `FunctionalChange` from baseline + current observation.
 *
 *   - If `baseline.baseline_provenance` is null and `baseline.independence`
 *     is `unknown`, the change is `unknown` and not care-relevant.
 *   - Cause is intentionally not a field. Never inferred.
 */
export function buildFunctionalChange(
  input: BuildFunctionalChangeInput,
): FunctionalChange {
  const from = input.baseline.independence;
  const to = input.current.observed_independence;
  let direction: FunctionalChange["direction"] = "unknown";
  if (from !== "unknown" && to !== "unknown") {
    if (isIndependenceDecline(from, to)) direction = "decline";
    else if (independenceRank(to) < independenceRank(from)) direction = "improvement";
    else direction = "unchanged";
  }
  const careRelevant =
    direction === "decline" && input.current.observed_independence !== "unknown";

  return {
    change_id: input.change_id,
    subject_id: input.subject_id,
    activity: input.activity,
    baseline: {
      independence: from,
      baseline_time: input.baseline.baseline_time,
      baseline_provenance: input.baseline.baseline_provenance,
    },
    current: {
      independence: to,
      current_time: input.current.observation_time,
      current_provenance: input.current.provenance,
    },
    direction,
    care_relevant: careRelevant,
  };
}

function independenceRank(level: IndependenceLevel): number {
  return INDEPENDENCE_ORDER.indexOf(level);
}

// ─── Active concern strength for a single activity ────────────────────────

/**
 * Independence ordering for change classification.
 * Exported for downstream engines that need to read INDEPENDENCE_ORDER
 * but should not redefine it.
 */
export { IndependenceLevelSchema };
