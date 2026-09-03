/**
 * Observation classifiers — extract cognitive, behavioral, functional, and
 * safety observations from source text.
 *
 * These classifiers do NOT:
 *   - Diagnose dementia.
 *   - Infer subtype.
 *   - Stage severity.
 *   - Convert "worse" into "dementia is progressing".
 *
 * They DO:
 *   - Classify a source statement into one of the observation types in
 *     ./types.ts.
 *   - Preserve the source attribution and strength.
 *   - Preserve the raw text exactly.
 *   - Tag the cognitive domain when relevant.
 *   - Distinguish wandering from purposeful travel from getting lost from
 *     elopement from unexpected departure — they are NOT interchangeable.
 *   - Distinguish forgetting (cognitive) from medication error (functional)
 *     from safety consequence (safety).
 *   - Distinguish prompting from supervision from assistance from
 *     dependence (delegated to ./independence.ts).
 *
 * The output is always an observation (or list of observations), never
 * an interpretation, a pattern, a situation, or a diagnosis.
 */

import type {
  BehavioralObservation,
  BehavioralObservationType,
  CognitiveDomain,
  CognitiveObservation,
  CognitiveObservationType,
  ConcernStrength,
  ConfusionObservation,
  FunctionalObservation,
  FunctionalObservationType,
  MedicationManagementAspect,
  OrientationAspect,
  Provenance,
  SafetyObservation,
  SafetyObservationType,
  SourceType,
} from "./types";
import { classifyIndependenceFromText } from "./independence";

// ─── Helpers ──────────────────────────────────────────────────────────────

let observationCounter = 0;
function newObservationId(prefix: string): string {
  observationCounter++;
  return `${prefix}_${Date.now()}_${observationCounter.toString(36)}`;
}

export type StrengthFromTextInput = {
  text: string;
  has_quantifier: boolean;
  has_explicit_specifics: boolean;
};

export function inferConcernStrength(input: StrengthFromTextInput): ConcernStrength {
  if (input.has_quantifier) return "quantified_event";
  if (input.has_explicit_specifics) return "specific_observation";
  if (/\b(seems|maybe|probably|might|could|often|sometimes|usually|worried|concerned)\b/i.test(input.text)) {
    return "vague_concern";
  }
  return "specific_observation";
}

const WORD_NUMBERS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5,
  six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, twenty: 20, fifty: 50, hundred: 100,
};

export function detectQuantifier(text: string): { count: number | null; window: { start: string | null; end: string | null } } {
  // Numeric
  const m = text.match(/\b(\d{1,3})\s*(?:times|x|occasions?)\b/i);
  let count: number | null = m ? parseInt(m[1], 10) : null;
  // Word number
  if (count === null) {
    const w = text.match(/\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|fifty|hundred)\b\s*(?:times|x|occasions?)\b/i);
    if (w) count = WORD_NUMBERS[w[1].toLowerCase()] ?? null;
  }
  const range = text.match(/\b(?:between|from)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|h)?)\s+(?:and|to|until|-)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|h)?)\b/i);
  if (range) {
    return { count, window: { start: range[1], end: range[2] } };
  }
  return { count, window: { start: null, end: null } };
}

// ─── Cognitive Observation Extraction ────────────────────────────────────

const COGNITIVE_PATTERNS: ReadonlyArray<{
  type: CognitiveObservationType;
  domain: CognitiveDomain;
  pattern: RegExp;
  orientation_aspect?: OrientationAspect;
}> = [
  { type: "repeated_question", domain: "memory", pattern: /\b(?:asked.*same.*(?:question|again|multiple times|like \d+ times)|keeps asking|repeatedly asks|asking again|asked (?:the )?same (?:question|thing) \d+ times)\b/i },
  { type: "lost_objects", domain: "memory", pattern: /\b(?:can't find|loses?|losing|can't remember where (?:she|he|they) put|misplaces?|loses? (?:her|his|their) (?:keys|glasses|wallet|phone)|forgot where (?:she|he|they) put)\b/i },
  { type: "forgot_recent_event", domain: "memory", pattern: /\b(?:forgot (?:that|the visit|the appointment|yesterday|last week)|doesn't remember (?:that|the visit|the appointment|yesterday))\b/i },
  { type: "forgot_instruction", domain: "memory", pattern: /\b(?:forgot (?:the|my|her|his|their) (?:instructions?|directions?)|didn't follow (?:the|my) (?:instructions?|directions?))\b/i },
  { type: "unable_to_retain", domain: "memory", pattern: /\b(?:can't (?:seem to )?(?:retain|hold onto|keep) (?:the )?(?:information|new (?:information|things))|within minutes|just told (?:her|him|them))\b/i },
  { type: "repeated_story", domain: "memory", pattern: /\b(?:keeps? telling (?:the )?same (?:story|thing)|told me (?:the )?same story)\b/i },
  { type: "word_finding", domain: "language", pattern: /\b(?:can't find the word|word[- ]?finding|trouble finding words|can't remember the word|can't say)\b/i },
  { type: "conversation_following", domain: "attention", pattern: /\b(?:can't (?:seem to )?follow (?:the )?(?:conversation|show|tv)|loses? track of (?:the )?conversation|trouble following)\b/i },
];

const ORIENTATION_PATTERNS: ReadonlyArray<{
  aspect: OrientationAspect;
  pattern: RegExp;
}> = [
  { aspect: "to_person", pattern: /\b(?:didn't recognize (?:me|her|him|his wife|her husband)|didn't know who (?:I|he|she) was)\b/i },
  { aspect: "to_place", pattern: /\b(?:didn't know where (?:she|he|they) (?:was|were)|didn't recognize (?:the )?(?:house|home|place)|thought (?:she|he) was (?:somewhere else|at work|at the (?:old|previous) (?:house|home)))\b/i },
  { aspect: "to_time", pattern: /\b(?:didn't know (?:the (?:day|date|time|year))|(?:thought|said) (?:it was|it is) (?:yesterday|years ago|in \d{4}))\b/i },
  { aspect: "to_situation", pattern: /\b(?:didn't understand why (?:we|they|I) (?:were|are) (?:there|here)|didn't know what was happening)\b/i },
];

export type ExtractCognitiveParams = {
  text: string;
  subject_id: string;
  source_type: SourceType;
  observer_id?: string | null;
  observed_at?: string | null;
  document_id?: string | null;
};

export function extractCognitiveObservations(
  params: ExtractCognitiveParams,
): CognitiveObservation[] {
  const observations: CognitiveObservation[] = [];
  const provenance = makeProvenance(params);

  for (const rule of COGNITIVE_PATTERNS) {
    if (rule.pattern.test(params.text)) {
      const q = detectQuantifier(params.text);
      const strength = inferConcernStrength({
        text: params.text,
        has_quantifier: q.count !== null,
        has_explicit_specifics: /specific|exact/.test(params.text) === false,
      });
      observations.push({
        observation_id: newObservationId("cog"),
        subject_id: params.subject_id,
        observation_type: rule.type,
        cognitive_domain: rule.domain,
        observed_behavior: params.text,
        concern_strength: strength,
        observation_confidence: strengthToConfidence(strength),
        observation_time: params.observed_at ?? null,
        provenance,
        orientation_aspect: rule.orientation_aspect ?? null,
        quantifier: q.count !== null ? { count: q.count, window_start: q.window.start, window_end: q.window.end } : null,
      });
    }
  }

  for (const rule of ORIENTATION_PATTERNS) {
    if (rule.pattern.test(params.text)) {
      observations.push({
        observation_id: newObservationId("orient"),
        subject_id: params.subject_id,
        observation_type: "forgot_recent_event",
        cognitive_domain: "orientation",
        observed_behavior: params.text,
        concern_strength: inferConcernStrength({
          text: params.text,
          has_quantifier: false,
          has_explicit_specifics: true,
        }),
        observation_confidence: "medium",
        observation_time: params.observed_at ?? null,
        provenance,
        orientation_aspect: rule.aspect,
        quantifier: null,
      });
    }
  }

  return observations;
}

// ─── Confusion Observation ────────────────────────────────────────────────

export type ExtractConfusionParams = {
  text: string;
  subject_id: string;
  source_type: SourceType;
  observer_id?: string | null;
  observed_at?: string | null;
  document_id?: string | null;
};

const CONFUSION_TRIGGER = /\b(?:confus(?:ed|ion)|disorient(?:ed|ation)|bewildered|not making sense|lost (?:in thought|track))\b/i;
const ONSET_ACUTE = /\b(?:suddenly|all of a sudden|out of (?:the )?blue|this morning|overnight|today|just now|acute)\b/i;
const ONSET_GRADUAL = /\b(?:gradually|over (?:the )?(?:past|last)|increasingly|lately|these days|over time|slowly)\b/i;
const CONTEXT_NIGHTTIME = /\b(?:at night|2 ?am|3 ?am|4 ?am|nighttime|midnight|overnight|evenings?|night)\b/i;
const CONTEXT_APPOINTMENT = /\b(?:at (?:the )?(?:appointment|doctor|clinic|hospital|visit)|during (?:the )?(?:appointment|visit|exam)|today's appointment|yesterday's appointment)\b/i;

export function extractConfusionObservations(
  params: ExtractConfusionParams,
): ConfusionObservation[] {
  if (!CONFUSION_TRIGGER.test(params.text)) return [];
  const provenance = makeProvenance(params);
  const onset = ONSET_ACUTE.test(params.text)
    ? "acute"
    : ONSET_GRADUAL.test(params.text)
    ? "gradual"
    : "unknown";
  const context = CONTEXT_NIGHTTIME.test(params.text)
    ? "nighttime"
    : CONTEXT_APPOINTMENT.test(params.text)
    ? "appointment"
    : null;
  const strength = inferConcernStrength({
    text: params.text,
    has_quantifier: false,
    has_explicit_specifics: /specific|exactly/.test(params.text),
  });
  return [
    {
      observation_id: newObservationId("conf"),
      subject_id: params.subject_id,
      observed_behavior: params.text,
      concern_strength: strength,
      observation_confidence: strengthToConfidence(strength),
      observation_time: params.observed_at ?? null,
      provenance,
      attributes: {
        onset,
        duration: null,
        context,
        trigger: null,
        behavior: params.text,
        resolution: null,
        recurrence: /\b(?:again|repeatedly|keeps|often)\b/i.test(params.text),
      },
    },
  ];
}

// ─── Behavioral Observation ───────────────────────────────────────────────

const BEHAVIORAL_PATTERNS: ReadonlyArray<{
  type: BehavioralObservationType;
  pattern: RegExp;
}> = [
  { type: "agitation", pattern: /\b(?:agitat\w*|aggressive|yelling|screaming|combative|irritable|restless)\b/i },
  { type: "withdrawal", pattern: /\b(?:withdrawn|withdrawing|isolating|won'?t talk|no longer engages|disengaged|stopped (?:going out|seeing friends))\b/i },
  { type: "aggression", pattern: /\b(?:hit|shoved|grabbed|bit|kicked|threw|aggressive|physical(?:ly)? (?:with|aggressive))\b/i },
  { type: "apathy", pattern: /\b(?:apathetic|apathy|no interest|doesn'?t care|unmotivated|flat affect)\b/i },
  { type: "repetitive_behavior", pattern: /\b(?:repetitiv\w*|paces?|pacing|repeated (?:action|behavior)|fidgeting)\b/i },
  { type: "nighttime_activity", pattern: /\b(?:up at (?:night|2 ?am|3 ?am|4 ?am)|wakes? (?:up )?at night|nighttime (?:waking|activity|wandering))\b/i },
  { type: "inappropriate_behavior", pattern: /\b(?:inappropriate|disinhibit|undressing|urinating|socially inappropriate|public)\b/i },
  { type: "routine_disruption", pattern: /\b(?:routine.*disrupt|disrupt.*routine|off schedule|out of routine|can'?t stick to routine)\b/i },
  { type: "sundowning_like_pattern", pattern: /\b(?:sundowning|sundown|evenings? (?:worsen|get worse|worse))\b/i },
];

export type ExtractBehavioralParams = {
  text: string;
  subject_id: string;
  source_type: SourceType;
  observer_id?: string | null;
  observed_at?: string | null;
  document_id?: string | null;
};

export function extractBehavioralObservations(
  params: ExtractBehavioralParams,
): BehavioralObservation[] {
  const observations: BehavioralObservation[] = [];
  const provenance = makeProvenance(params);
  for (const rule of BEHAVIORAL_PATTERNS) {
    if (rule.pattern.test(params.text)) {
      const strength = inferConcernStrength({
        text: params.text,
        has_quantifier: false,
        has_explicit_specifics: true,
      });
      const context_tags: string[] = [];
      if (CONTEXT_NIGHTTIME.test(params.text)) context_tags.push("nighttime");
      if (CONTEXT_APPOINTMENT.test(params.text)) context_tags.push("appointment");
      observations.push({
        observation_id: newObservationId("beh"),
        subject_id: params.subject_id,
        observation_type: rule.type,
        observed_behavior: params.text,
        concern_strength: strength,
        observation_confidence: strengthToConfidence(strength),
        observation_time: params.observed_at ?? null,
        provenance,
        context_tags,
      });
    }
  }
  return observations;
}

// ─── Functional Observation ───────────────────────────────────────────────

const FUNCTIONAL_ACTIVITY_PATTERNS: ReadonlyArray<{
  activity: FunctionalObservationType;
  pattern: RegExp;
}> = [
  { activity: "bathing", pattern: /\b(?:bathing|showering|shower|bath)\b/i },
  { activity: "dressing", pattern: /\b(?:dressing|getting dressed|get dressed|choosing (?:clothes|outfits?)|getting (?:her|him|them) dressed|to dress)\b/i },
  { activity: "toileting", pattern: /\b(?:toileting|toilet|incontin\w*|bathroom)\b/i },
  { activity: "eating", pattern: /\b(?:eating|feeding|meals?)\b/i },
  { activity: "mobility", pattern: /\b(?:walking|mobility|getting (?:up|around)|stair|transfers?)\b/i },
  { activity: "grooming", pattern: /\b(?:grooming|brushing|hair|nails|teeth)\b/i },
  { activity: "medication_self_management", pattern: /\b(?:medications?|meds?|pills?|pillbox)\b/i },
  { activity: "finances", pattern: /\b(?:finances?|bills?|banking|money)\b/i },
  { activity: "transportation", pattern: /\b(?:driving|car|transport|getting (?:her|him|them) (?:to|around))\b/i },
  { activity: "cooking", pattern: /\b(?:cooking|meals? (?:prep|preparation)|stove|kitchen)\b/i },
  { activity: "shopping", pattern: /\b(?:shopping|groceries)\b/i },
  { activity: "appointments", pattern: /\b(?:appointments?|schedules?)\b/i },
  { activity: "communication_device", pattern: /\b(?:phone|computer|email|calling|texting)\b/i },
  { activity: "household_management", pattern: /\b(?:household|housework|chores|cleaning|laundry)\b/i },
];

const MEDICATION_MGMT_PATTERNS: ReadonlyArray<{
  aspect: MedicationManagementAspect;
  pattern: RegExp;
}> = [
  { aspect: "forgot_to_take", pattern: /\b(?:forgot (?:to take|her|his|her) (?:med(?:ication|s)?|pills?)|didn't take (?:her|his) (?:med|meds|pills?))\b/i },
  { aspect: "double_dosed", pattern: /\b(?:took (?:it|the (?:med(?:ication)?|pill)) (?:twice|again)|double[- ]?dos\w*|took (?:her|his|their) (?:morning|evening|night)?\s*medication twice)\b/i },
  { aspect: "missed_doses", pattern: /\b(?:missed (?:a |her |his )?(?:dose|med(?:ication)?))\b/i },
  { aspect: "unable_to_organize", pattern: /\b(?:can't (?:organize|set up|fill) (?:her|his|the) pillbox|pillbox|organize (?:her|his) medications?)\b/i },
  { aspect: "requires_caregiver_administration", pattern: /\b(?:I (?:give|administer|fill|set up)|(?:she|he|they) needs? (?:me|us) to (?:give|administer|fill|set up)) (?:her|his|the) (?:med(?:ication|s)?|pills?)\b/i },
  { aspect: "requires_caregiver_prompting", pattern: /\b(?:I (?:have to|need to) (?:remind|prompt) (?:her|him|them) (?:to take|about) (?:her|his|their) (?:med(?:ication|s)?|pills?))\b/i },
  { aspect: "requires_caregiver_setup", pattern: /\b(?:I (?:set up|fill|prepare) (?:her|his|the) pillbox|set up (?:her|his) medications?)\b/i },
];

export type ExtractFunctionalParams = {
  text: string;
  subject_id: string;
  source_type: SourceType;
  observer_id?: string | null;
  observed_at?: string | null;
  document_id?: string | null;
};

export function extractFunctionalObservations(
  params: ExtractFunctionalParams,
): FunctionalObservation[] {
  const observations: FunctionalObservation[] = [];
  const provenance = makeProvenance(params);
  const indep = classifyIndependenceFromText(params.text);
  const matchedActivity = FUNCTIONAL_ACTIVITY_PATTERNS.find((a) => a.pattern.test(params.text));
  if (!matchedActivity) return observations;
  const strength = inferConcernStrength({
    text: params.text,
    has_quantifier: false,
    has_explicit_specifics: /now|used to|before/.test(params.text),
  });
  const medAspect =
    matchedActivity.activity === "medication_self_management"
      ? MEDICATION_MGMT_PATTERNS.find((p) => p.pattern.test(params.text))?.aspect ?? null
      : null;
  observations.push({
    observation_id: newObservationId("func"),
    subject_id: params.subject_id,
    activity: matchedActivity.activity,
    observed_independence: indep.level,
    observed_behavior: params.text,
    concern_strength: strength,
    observation_confidence: strengthToConfidence(strength),
    observation_time: params.observed_at ?? null,
    provenance,
    medication_aspect: medAspect,
    baseline_present: /\b(?:used to|before|previously|was able to)\b/i.test(params.text),
  });
  return observations;
}

// ─── Safety Observation ──────────────────────────────────────────────────

const SAFETY_PATTERNS: ReadonlyArray<{
  type: SafetyObservationType;
  pattern: RegExp;
}> = [
  // Distinct semantics — never interchangeable
  { type: "wandering", pattern: /\b(?:wander(?:s|ing|ed)?|paced? purposelessly?|repetitive (?:walking|locomotion))\b/i },
  { type: "elopement", pattern: /\b(?:eloped?|elopement|left (?:a |the )?(?:supervised|secure|locked|facility)|slipped (?:out|away) from (?:a |the )?(?:supervised|secure|facility))\b/i },
  { type: "getting_lost", pattern: /\b(?:got lost|could(?:n't| not) find (?:her|his|their|the) way (?:back|home)|lost (?:on|in) (?:a |the )?(?:familiar|known) (?:place|route))\b/i },
  { type: "navigation_impairment", pattern: /\b(?:can't (?:navigate|find (?:her|his|their) way)|forgets? (?:the|how to get (?:to|home)) (?:way|route)|familiar (?:route|place) (?:issue|problem|confusion))\b/i },
  { type: "unexpected_departure", pattern: /\b(?:left (?:the )?house (?:at|in|on) (?:2|3|4) ?am|left home unexpectedly|left (?:the )?house at (?:night|an unusual))\b/i },
  { type: "purposeful_travel", pattern: /\b(?:walks? (?:around|in) the neighborhood (?:every|daily|each)|purposeful(?:ly)? (?:left|walks?)|goes? for (?:a )?walk (?:every|daily))\b/i },
  { type: "stove_left_on", pattern: /\b(?:stove (?:was )?left (?:on|burning)|burner (?:left|was left) on|forgot (?:to turn (?:off|the stove)|the stove))\b/i },
  { type: "unsafe_driving", pattern: /\b(?:driv(?:ing|e) (?:concern|issues?|problem|confused)|got lost (?:while|in) driv(?:ing|e)|unsafe (?:driver|driving))\b/i },
  { type: "hazard_nonresponse", pattern: /\b(?:didn't respond (?:to|when)|didn't (?:notice|react to)) (?:the |a )?(?:fire|smoke|alarm|car honking|siren)\b/i },
  { type: "door_unlocked", pattern: /\b(?:door (?:left )?unlocked|forgot to lock|left the (?:front |back )?door (?:open|unlocked))\b/i },
  { type: "unsafe_medication_use", pattern: /\b(?:took (?:wrong|extra) (?:med(?:ication)?|pill)|unsafe medication (?:use|use)|mixed (?:up|confused) (?:her|his) (?:meds|medications))\b/i },
];

export type ExtractSafetyParams = {
  text: string;
  subject_id: string;
  source_type: SourceType;
  observer_id?: string | null;
  observed_at?: string | null;
  document_id?: string | null;
};

export function extractSafetyObservations(
  params: ExtractSafetyParams,
): SafetyObservation[] {
  const observations: SafetyObservation[] = [];
  const provenance = makeProvenance(params);
  for (const rule of SAFETY_PATTERNS) {
    if (rule.pattern.test(params.text)) {
      const strength = inferConcernStrength({
        text: params.text,
        has_quantifier: false,
        has_explicit_specifics: /specific|exact/.test(params.text) === false,
      });
      const temporal_class: SafetyObservation["temporal_class"] = ONSET_ACUTE.test(params.text)
        ? "acute"
        : /\b(?:often|repeatedly|keeps|every night|twice a week|each week)\b/i.test(params.text)
        ? "recurrent"
        : "unknown";
      let care_consequence: SafetyObservation["care_consequence"] = "unknown";
      if (rule.type === "wandering" || rule.type === "elopement" || rule.type === "unexpected_departure") {
        care_consequence = "increased_supervision";
      } else if (rule.type === "stove_left_on" || rule.type === "unsafe_driving" || rule.type === "hazard_nonresponse") {
        care_consequence = "active_monitoring";
      } else if (rule.type === "navigation_impairment" || rule.type === "getting_lost") {
        care_consequence = "increased_supervision";
      }
      observations.push({
        observation_id: newObservationId("safe"),
        subject_id: params.subject_id,
        observation_type: rule.type,
        observed_behavior: params.text,
        concern_strength: strength,
        observation_confidence: strengthToConfidence(strength),
        observation_time: params.observed_at ?? null,
        provenance,
        temporal_class,
        care_consequence,
      });
    }
  }
  return observations;
}

// ─── Helpers ──────────────────────────────────────────────────────────────

function makeProvenance(params: {
  text: string;
  source_type: SourceType;
  observer_id?: string | null;
  observed_at?: string | null;
  document_id?: string | null;
}): Provenance {
  return {
    source_type: params.source_type,
    observer_id: params.observer_id ?? null,
    observed_at: params.observed_at ?? null,
    raw_text: params.text,
    captured_at: new Date().toISOString(),
    source_authored_at: null,
    document_id: params.document_id ?? null,
  };
}

function strengthToConfidence(strength: ConcernStrength): "low" | "medium" | "high" {
  if (strength === "quantified_event") return "high";
  if (strength === "specific_observation") return "medium";
  if (strength === "clinically_documented") return "high";
  return "low";
}
