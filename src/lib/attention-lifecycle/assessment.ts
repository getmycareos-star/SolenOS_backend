/**
 * Multidimensional Attention Assessment Engine
 * 
 * Assesses attention using 16+ dimensions beyond simple severity.
 * Prevents simplistic "Important symptom → alert" patterns.
 * 
 * CARE-DOMAIN CALIBRATION:
 * - Appetite/nutrition: Weight loss risk, dehydration, functional decline cascade
 * - Confusion/cognition: Delirium risk, safety risk, medication toxicity
 * - Mobility/falls: Injury risk, loss of independence, fear of falling
 * - Sleep: Delirium precipitant, caregiver burnout driver, immune function
 * - Mood/behavior: Depression, agitation, wandering, caregiver strain
 * - Medication: Adherence, side effects, interactions, prescribing cascade
 * - Elimination: UTI risk, constipation, incontinence, dignity
 * - Skin integrity: Pressure injury, infection, mobility proxy
 * - Pain: Under-detected in dementia, drives behavior changes
 * - Social engagement: Isolation, cognitive reserve, quality of life
 */

import {
  AttentionDimension,
  AttentionDimensionScore,
  AttentionAssessment,
  AttentionState,
  DEFAULT_ATTENTION_LIFECYCLE_CONFIG,
  AttentionLifecycleConfig,
  CareDomain,
  CareDomainSignal,
} from "./types";

export const CARE_DOMAIN_SIGNALS: CareDomainSignal[] = [
  // APPETITE / NUTRITION
  { domain: "appetite_nutrition", signal_type: "reduced_intake", description: "Eating less than usual", baseline_deviation: "moderate", clinical_significance: "medium", typical_escalation_timeline_hours: 48, requires_clinical_input: false, common_cascades: ["mobility_falls", "confusion_cognition", "skin_integrity"] },
  { domain: "appetite_nutrition", signal_type: "minimal_intake", description: "Only few bites per meal", baseline_deviation: "severe", clinical_significance: "high", typical_escalation_timeline_hours: 24, requires_clinical_input: true, common_cascades: ["mobility_falls", "confusion_cognition", "elimination", "skin_integrity"] },
  { domain: "appetite_nutrition", signal_type: "refusal_eat", description: "Refusing meals entirely", baseline_deviation: "severe", clinical_significance: "high", typical_escalation_timeline_hours: 12, requires_clinical_input: true, common_cascades: ["confusion_cognition", "mood_behavior", "medication"] },
  { domain: "appetite_nutrition", signal_type: "weight_loss", description: "Noticeable weight loss", baseline_deviation: "moderate", clinical_significance: "high", typical_escalation_timeline_hours: 168, requires_clinical_input: true, common_cascades: ["mobility_falls", "skin_integrity", "confusion_cognition"] },
  { domain: "appetite_nutrition", signal_type: "dehydration_signs", description: "Dry mouth, low urine, confusion", baseline_deviation: "severe", clinical_significance: "critical", typical_escalation_timeline_hours: 6, requires_clinical_input: true, common_cascades: ["confusion_cognition", "elimination", "medication"] },

  // CONFUSION / COGNITION
  { domain: "confusion_cognition", signal_type: "mild_confusion", description: "Mild disorientation, repetitive questions", baseline_deviation: "mild", clinical_significance: "medium", typical_escalation_timeline_hours: 72, requires_clinical_input: false, common_cascades: ["mood_behavior", "sleep", "medication"] },
  { domain: "confusion_cognition", signal_type: "acute_confusion", description: "Sudden severe confusion, not recognizing family", baseline_deviation: "severe", clinical_significance: "critical", typical_escalation_timeline_hours: 2, requires_clinical_input: true, common_cascades: ["medication", "elimination", "pain", "appetite_nutrition"] },
  { domain: "confusion_cognition", signal_type: "sundowning", description: "Worsening confusion late afternoon/evening", baseline_deviation: "moderate", clinical_significance: "medium", typical_escalation_timeline_hours: 48, requires_clinical_input: false, common_cascades: ["sleep", "mood_behavior", "mobility_falls"] },
  { domain: "confusion_cognition", signal_type: "memory_decline", description: "Noticeable worsening of short-term memory", baseline_deviation: "moderate", clinical_significance: "medium", typical_escalation_timeline_hours: 168, requires_clinical_input: true, common_cascades: ["medication", "mood_behavior", "social_engagement"] },

  // ACUTE NEUROLOGICAL / STROKE
  { domain: "confusion_cognition", signal_type: "stroke_symptoms", description: "Sudden weakness, slurred speech, facial droop, vision loss", baseline_deviation: "severe", clinical_significance: "critical", typical_escalation_timeline_hours: 1, requires_clinical_input: true, common_cascades: ["mobility_falls", "confusion_cognition", "medication", "swallowing_risk"] },
  { domain: "confusion_cognition", signal_type: "tIA_symptoms", description: "Transient weakness, numbness, speech difficulty resolving", baseline_deviation: "severe", clinical_significance: "high", typical_escalation_timeline_hours: 6, requires_clinical_input: true, common_cascades: ["mobility_falls", "confusion_cognition", "medication"] },
  { domain: "confusion_cognition", signal_type: "seizure_activity", description: "Convulsions, staring spells, loss of awareness", baseline_deviation: "severe", clinical_significance: "critical", typical_escalation_timeline_hours: 1, requires_clinical_input: true, common_cascades: ["mobility_falls", "confusion_cognition", "medication", "skin_integrity"] },

  // MOBILITY / FALLS
  { domain: "mobility_falls", signal_type: "unsteady_gait", description: "Unsteady walking, holding furniture", baseline_deviation: "moderate", clinical_significance: "medium", typical_escalation_timeline_hours: 48, requires_clinical_input: false, common_cascades: ["confusion_cognition", "medication", "pain"] },
  { domain: "mobility_falls", signal_type: "near_fall", description: "Almost fell, caught self", baseline_deviation: "moderate", clinical_significance: "high", typical_escalation_timeline_hours: 24, requires_clinical_input: true, common_cascades: ["fear_of_falling", "confusion_cognition", "medication"] },
  { domain: "mobility_falls", signal_type: "fall_no_injury", description: "Fell but no apparent injury", baseline_deviation: "severe", clinical_significance: "high", typical_escalation_timeline_hours: 12, requires_clinical_input: true, common_cascades: ["skin_integrity", "pain", "confusion_cognition", "fear_of_falling"] },
  { domain: "mobility_falls", signal_type: "fall_with_injury", description: "Fell with injury (bruise, cut, fracture)", baseline_deviation: "severe", clinical_significance: "critical", typical_escalation_timeline_hours: 1, requires_clinical_input: true, common_cascades: ["skin_integrity", "pain", "mobility_falls", "confusion_cognition"] },
  { domain: "mobility_falls", signal_type: "fear_of_falling", description: "Afraid to walk, limiting activity", baseline_deviation: "moderate", clinical_significance: "medium", typical_escalation_timeline_hours: 72, requires_clinical_input: false, common_cascades: ["mobility_falls", "social_engagement", "mood_behavior"] },

  // SLEEP
  { domain: "sleep", signal_type: "difficulty_falling_asleep", description: "Trouble falling asleep", baseline_deviation: "mild", clinical_significance: "low", typical_escalation_timeline_hours: 168, requires_clinical_input: false, common_cascades: ["mood_behavior", "confusion_cognition", "appetite_nutrition"] },
  { domain: "sleep", signal_type: "night_waking", description: "Waking multiple times at night", baseline_deviation: "moderate", clinical_significance: "medium", typical_escalation_timeline_hours: 72, requires_clinical_input: false, common_cascades: ["confusion_cognition", "mood_behavior", "falls_risk", "caregiver_burnout"] },
  { domain: "sleep", signal_type: "day_night_reversal", description: "Sleeping all day, awake all night", baseline_deviation: "severe", clinical_significance: "high", typical_escalation_timeline_hours: 48, requires_clinical_input: true, common_cascades: ["confusion_cognition", "mood_behavior", "appetite_nutrition", "social_engagement"] },

  // MOOD / BEHAVIOR
  { domain: "mood_behavior", signal_type: "apathy_withdrawal", description: "No interest in usual activities", baseline_deviation: "moderate", clinical_significance: "medium", typical_escalation_timeline_hours: 72, requires_clinical_input: false, common_cascades: ["appetite_nutrition", "social_engagement", "mobility_falls"] },
  { domain: "mood_behavior", signal_type: "agitation_aggression", description: "Restless, pacing, verbal/physical aggression", baseline_deviation: "severe", clinical_significance: "high", typical_escalation_timeline_hours: 12, requires_clinical_input: true, common_cascades: ["confusion_cognition", "pain", "medication", "sleep"] },
  { domain: "mood_behavior", signal_type: "anxiety_fear", description: "Excessive worry, fear of being alone", baseline_deviation: "moderate", clinical_significance: "medium", typical_escalation_timeline_hours: 48, requires_clinical_input: false, common_cascades: ["sleep", "mobility_falls", "social_engagement"] },
  { domain: "mood_behavior", signal_type: "depressive_symptoms", description: "Tearful, hopeless, self-neglect", baseline_deviation: "moderate", clinical_significance: "high", typical_escalation_timeline_hours: 48, requires_clinical_input: true, common_cascades: ["appetite_nutrition", "medication", "social_engagement", "self_harm_risk"] },

  // MEDICATION
  { domain: "medication", signal_type: "missed_doses", description: "Missing scheduled medications", baseline_deviation: "moderate", clinical_significance: "high", typical_escalation_timeline_hours: 24, requires_clinical_input: true, common_cascades: ["confusion_cognition", "mobility_falls", "mood_behavior", "pain"] },
  { domain: "medication", signal_type: "side_effects", description: "New symptoms after medication change", baseline_deviation: "moderate", clinical_significance: "high", typical_escalation_timeline_hours: 12, requires_clinical_input: true, common_cascades: ["confusion_cognition", "mobility_falls", "appetite_nutrition", "elimination"] },
  { domain: "medication", signal_type: "wrong_dose", description: "Taking wrong amount or frequency", baseline_deviation: "severe", clinical_significance: "critical", typical_escalation_timeline_hours: 2, requires_clinical_input: true, common_cascades: ["confusion_cognition", "mobility_falls", "mood_behavior"] },
  { domain: "medication", signal_type: "new_medication", description: "Started new medication recently", baseline_deviation: "mild", clinical_significance: "medium", typical_escalation_timeline_hours: 72, requires_clinical_input: false, common_cascades: ["confusion_cognition", "appetite_nutrition", "sleep", "elimination"] },

  // ELIMINATION
  { domain: "elimination", signal_type: "constipation", description: "No bowel movement 3+ days", baseline_deviation: "moderate", clinical_significance: "medium", typical_escalation_timeline_hours: 48, requires_clinical_input: true, common_cascades: ["appetite_nutrition", "confusion_cognition", "pain", "mobility_falls"] },
  { domain: "elimination", signal_type: "incontinence_new", description: "New urinary or fecal incontinence", baseline_deviation: "severe", clinical_significance: "high", typical_escalation_timeline_hours: 24, requires_clinical_input: true, common_cascades: ["skin_integrity", "confusion_cognition", "mobility_falls", "social_engagement"] },
  { domain: "elimination", signal_type: "uti_signs", description: "Burning, frequency, confusion, fever", baseline_deviation: "severe", clinical_significance: "critical", typical_escalation_timeline_hours: 6, requires_clinical_input: true, common_cascades: ["confusion_cognition", "mobility_falls", "appetite_nutrition", "dehydration"] },

  // SKIN INTEGRITY
  { domain: "skin_integrity", signal_type: "redness_pressure", description: "Red area over bony prominence", baseline_deviation: "moderate", clinical_significance: "high", typical_escalation_timeline_hours: 24, requires_clinical_input: true, common_cascades: ["mobility_falls", "pain", "infection_risk"] },
  { domain: "skin_integrity", signal_type: "open_wound", description: "Skin tear, abrasion, pressure ulcer", baseline_deviation: "severe", clinical_significance: "high", typical_escalation_timeline_hours: 12, requires_clinical_input: true, common_cascades: ["infection_risk", "pain", "mobility_falls"] },

  // PAIN
  { domain: "pain", signal_type: "verbal_pain", description: "Saying hurts, grimacing, guarding", baseline_deviation: "moderate", clinical_significance: "high", typical_escalation_timeline_hours: 12, requires_clinical_input: true, common_cascades: ["mobility_falls", "mood_behavior", "sleep", "appetite_nutrition"] },
  { domain: "pain", signal_type: "behavioral_pain", description: "Agitation, resistance to care, pacing (non-verbal)", baseline_deviation: "moderate", clinical_significance: "high", typical_escalation_timeline_hours: 12, requires_clinical_input: true, common_cascades: ["confusion_cognition", "mood_behavior", "mobility_falls", "sleep"] },

  // SOCIAL ENGAGEMENT
  { domain: "social_engagement", signal_type: "isolation", description: "No visitors, not leaving room", baseline_deviation: "moderate", clinical_significance: "medium", typical_escalation_timeline_hours: 168, requires_clinical_input: false, common_cascades: ["mood_behavior", "confusion_cognition", "mobility_falls"] },
  { domain: "social_engagement", signal_type: "activity_cessation", description: "Stopped hobbies, groups, routines", baseline_deviation: "moderate", clinical_significance: "medium", typical_escalation_timeline_hours: 168, requires_clinical_input: false, common_cascades: ["mood_behavior", "cognitive_decline", "mobility_falls"] },
];

export type AssessmentContext = {
  care_recipient_id: string;
  signal: string;
  baseline: string;
  history: string[];
  caregiver_capacity: "high" | "medium" | "low" | "unknown";
  clinical_context: string;
  existing_items: string[];
  time_since_onset_ms: number;
  recurrence_count: number;
  trend: "worsening" | "improving" | "stable" | "unknown";
  action_taken: boolean;
  action_description?: string;
  evidence_quality: "high" | "medium" | "low";
  open_loops: string[];
  uncertainty_level: number;
  compositeScore?: number;
  detected_domains?: CareDomainSignal[];
  clinical_risk_score?: number;
};

function clamp01(n: number): number {
  return Math.max(0, Math.min(1, n));
}

function scoreMagnitude(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  const signal = context.signal.toLowerCase();
  
  if (/\b(fall|seizure|stroke|heart attack|unconscious|not breathing|severe bleeding)\b/i.test(signal)) {
    score = 0.9;
  } else if (/\b(confusion|weakness|fever|pain|shortness of breath|chest pain)\b/i.test(signal)) {
    score = 0.7;
  } else if (/\b(appetite|sleep|mood|energy|mobility)\b/i.test(signal)) {
    score = 0.5;
  } else if (/\b(routine|refill|appointment|check[- ]?up)\b/i.test(signal)) {
    score = 0.2;
  } else {
    score = 0.4;
  }

  return {
    dimension: "magnitude",
    score: clamp01(score),
    evidence: `Signal: ${context.signal}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.magnitude,
  };
}

function scoreAcuteness(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  const hoursSinceOnset = context.time_since_onset_ms / 3600000;

  if (hoursSinceOnset < 1) score = 0.95;
  else if (hoursSinceOnset < 6) score = 0.8;
  else if (hoursSinceOnset < 24) score = 0.6;
  else if (hoursSinceOnset < 72) score = 0.4;
  else if (hoursSinceOnset < 168) score = 0.25;
  else score = 0.1;

  return {
    dimension: "acuteness",
    score: clamp01(score),
    evidence: `Time since onset: ${hoursSinceOnset.toFixed(1)} hours`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.acuteness,
  };
}

function scoreRisk(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  const signal = context.signal.toLowerCase();
  
  if (/\b(not breathing|unconscious|seizure|stroke|active bleeding|suicid)\b/i.test(signal)) {
    score = 0.95;
  } else if (/\b(fall|chest pain|shortness of breath|severe confusion|rapidly worsening)\b/i.test(signal)) {
    score = 0.8;
  } else if (/\b(confusion|weakness|dehydration|infection|missed medication)\b/i.test(signal)) {
    score = 0.6;
  } else if (/\b(appetite|sleep|mobility|mood)\b/i.test(signal)) {
    score = 0.4;
  } else {
    score = 0.2;
  }

  return {
    dimension: "risk",
    score: clamp01(score),
    evidence: `Risk indicators in signal: ${context.signal}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.risk,
  };
}

function scoreBaselineDeviation(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  const signal = context.signal.toLowerCase();
  const baseline = context.baseline.toLowerCase();

  if (/\b(sudden|suddenly|abrupt|acute|rapid)\b/i.test(signal)) {
    score = 0.8;
  } else if (context.history.length > 0) {
    const recentSimilar = context.history.filter(h => 
      h.toLowerCase().includes(signal.split(/\s+/)[0]) || 
      signal.split(/\s+/).some(w => h.toLowerCase().includes(w))
    ).length;
    
    if (recentSimilar === 0) score = 0.7;
    else if (recentSimilar < 3) score = 0.4;
    else score = 0.2;
  } else {
    score = 0.5;
  }

  return {
    dimension: "baseline_deviation",
    score: clamp01(score),
    evidence: `Baseline comparison: ${context.baseline}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.baseline_deviation,
  };
}

function scorePersistence(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  const hoursSinceOnset = context.time_since_onset_ms / 3600000;

  if (hoursSinceOnset >= 168) score = 0.9;
  else if (hoursSinceOnset >= 72) score = 0.7;
  else if (hoursSinceOnset >= 24) score = 0.5;
  else if (hoursSinceOnset >= 6) score = 0.3;
  else score = 0.1;

  return {
    dimension: "persistence",
    score: clamp01(score),
    evidence: `Duration: ${hoursSinceOnset.toFixed(1)} hours`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.persistence,
  };
}

function scoreRecurrence(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  
  if (context.recurrence_count >= 5) score = 0.9;
  else if (context.recurrence_count >= 3) score = 0.7;
  else if (context.recurrence_count >= 2) score = 0.5;
  else if (context.recurrence_count >= 1) score = 0.3;
  else score = 0.1;

  return {
    dimension: "recurrence",
    score: clamp01(score),
    evidence: `Recurrence count: ${context.recurrence_count}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.recurrence,
  };
}

function scoreTrend(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  
  switch (context.trend) {
    case "worsening": score = 0.8; break;
    case "improving": score = 0.2; break;
    case "stable": score = 0.4; break;
    case "unknown": default: score = 0.5;
  }

  return {
    dimension: "trend",
    score: clamp01(score),
    evidence: `Trend: ${context.trend}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.trend,
  };
}

function scoreFunctionalImpact(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  const signal = context.signal.toLowerCase();

  if (/\b(cannot|unable to|lost ability|bedridden|immobile|incontinent)\b/i.test(signal)) {
    score = 0.9;
  } else if (/\b(difficulty|trouble|struggling|needs help|assistance required)\b/i.test(signal)) {
    score = 0.6;
  } else if (/\b(slower|reduced|less|decreased|declining)\b/i.test(signal)) {
    score = 0.4;
  } else {
    score = 0.2;
  }

  return {
    dimension: "functional_impact",
    score: clamp01(score),
    evidence: `Functional indicators: ${context.signal}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.functional_impact,
  };
}

function scoreCognitiveBehavioralImpact(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  const signal = context.signal.toLowerCase();

  if (/\b(severe confusion|delirium|hallucination|aggression|wandering|sundowning)\b/i.test(signal)) {
    score = 0.9;
  } else if (/\b(confusion|memory|forgetful|disoriented|personality change|mood swing)\b/i.test(signal)) {
    score = 0.6;
  } else if (/\b(anxious|depressed|withdrawn|apathetic|irritable)\b/i.test(signal)) {
    score = 0.4;
  } else {
    score = 0.1;
  }

  return {
    dimension: "cognitive_behavioral_impact",
    score: clamp01(score),
    evidence: `Cognitive/behavioral indicators: ${context.signal}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.cognitive_behavioral_impact,
  };
}

function scoreMedicationImplications(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  const signal = context.signal.toLowerCase();

  if (/\b(overdose|toxicity|adverse reaction|allergic|missed dose|wrong dose|interaction)\b/i.test(signal)) {
    score = 0.9;
  } else if (/\b(new medication|dose change|medication review|pharmacy|prescription)\b/i.test(signal)) {
    score = 0.6;
  } else if (/\b(medication|pill|drug|prescription)\b/i.test(signal)) {
    score = 0.3;
  } else {
    score = 0.1;
  }

  return {
    dimension: "medication_implications",
    score: clamp01(score),
    evidence: `Medication relevance: ${context.signal}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.medication_implications,
  };
}

function scoreCaregiverCapacity(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  
  switch (context.caregiver_capacity) {
    case "low": score = 0.8; break;
    case "medium": score = 0.5; break;
    case "high": score = 0.2; break;
    case "unknown": default: score = 0.5;
  }

  return {
    dimension: "caregiver_capacity",
    score: clamp01(score),
    evidence: `Caregiver capacity: ${context.caregiver_capacity}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.caregiver_capacity,
  };
}

function scoreOpenLoopDependency(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  
  if (context.open_loops.length >= 3) score = 0.9;
  else if (context.open_loops.length >= 2) score = 0.7;
  else if (context.open_loops.length >= 1) score = 0.5;
  else score = 0.1;

  return {
    dimension: "open_loop_dependency",
    score: clamp01(score),
    evidence: `Open loops: ${context.open_loops.join(", ") || "none"}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.open_loop_dependency,
  };
}

function scoreTimePressure(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  const signal = context.signal.toLowerCase();

  if (/\b(now|immediately|urgent|emergency|asap|tonight|today)\b/i.test(signal)) {
    score = 0.9;
  } else if (/\b(this week|soon|within days|before weekend)\b/i.test(signal)) {
    score = 0.6;
  } else if (/\b(next week|this month|eventually)\b/i.test(signal)) {
    score = 0.3;
  } else {
    score = 0.2;
  }

  return {
    dimension: "time_pressure",
    score: clamp01(score),
    evidence: `Time pressure indicators: ${context.signal}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.time_pressure,
  };
}

function scoreUncertainty(context: AssessmentContext): AttentionDimensionScore {
  let score = clamp01(context.uncertainty_level);

  return {
    dimension: "uncertainty",
    score,
    evidence: `Uncertainty level: ${context.uncertainty_level}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.uncertainty,
  };
}

function scoreEvidenceQuality(context: AssessmentContext): AttentionDimensionScore {
  let score = 0;
  
  switch (context.evidence_quality) {
    case "high": score = 0.8; break;
    case "medium": score = 0.5; break;
    case "low": score = 0.2; break;
  }

  return {
    dimension: "evidence_quality",
    score: clamp01(score),
    evidence: `Evidence quality: ${context.evidence_quality}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.evidence_quality,
  };
}

function scoreActionAlreadyTaken(context: AssessmentContext): AttentionDimensionScore {
  let score = context.action_taken ? 0.7 : 0.2;
  
  if (context.action_taken && context.action_description) {
    if (/\b(contacted|called|scheduled|arranged|started|began|initiated)\b/i.test(context.action_description)) {
      score = 0.8;
    }
  }

  return {
    dimension: "action_already_taken",
    score: clamp01(score),
    evidence: context.action_taken 
      ? `Action taken: ${context.action_description || "yes"}` 
      : "No action taken yet",
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.action_already_taken,
  };
}

/**
 * Detect care domains from signal text and enrich context with clinical calibration
 */
function detectCareDomains(context: AssessmentContext): CareDomainSignal[] {
  const signal = context.signal.toLowerCase();
  const matched: CareDomainSignal[] = [];
  
  // Check for negation/resolution phrases that indicate benign variation or resolution
  const negationPhrases = [
    "but normal", "but had normal", "then normal", "returned to normal",
    "back to normal", "resolved", "recovered", "improved", "better now",
    "eating normally", "ate normally", "normal again", "no longer"
  ];
  const hasNegation = negationPhrases.some(phrase => signal.includes(phrase));
  
  for (const domainSignal of CARE_DOMAIN_SIGNALS) {
    const patterns = getDomainPatterns(domainSignal.domain, domainSignal.signal_type);
    for (const pattern of patterns) {
      if (pattern.test(signal)) {
        // Skip if signal contains negation phrases (benign variation/resolution)
        if (hasNegation && domainSignal.clinical_significance !== "critical") {
          continue;
        }
        matched.push(domainSignal);
        break;
      }
    }
  }
  
  return matched;
}

function getDomainPatterns(domain: CareDomain, signalType: string): RegExp[] {
  const patternMap: Record<string, RegExp[]> = {
    // Appetite/Nutrition
    "appetite_nutrition_reduced_intake": [/\b(ate less|eating less|reduced appetite|poor appetite|smaller portions|eating very little|very little (?:food|meal))\b/i],
    "appetite_nutrition_minimal_intake": [/\b(few bites|barely eating|hardly eating|minimal intake|only a few bites|hardly ate|barely ate|ate very little)\b/i],
    "appetite_nutrition_refusal_eat": [/\b(refus(?:e|ing) (?:to )?eat|won't eat|not eating|food refusal)\b/i],
    "appetite_nutrition_weight_loss": [/\b(weight loss|lost weight|thinner|loose clothes|waist smaller)\b/i],
    "appetite_nutrition_dehydration_signs": [/\b(dehydrat|dry mouth|low urine|dark urine|sunken eyes|skin tenting)\b/i],
    
    // Confusion/Cognition
    "confusion_cognition_mild_confusion": [/\b(mild confusion|a bit confused|somewhat confused|repetitive questions|same question)\b/i],
    "confusion_cognition_acute_confusion": [/\b(sudden confusion|acute confusion|severe confusion|not recogniz|doesn't know (?:who|where)|disoriented)\b/i],
    "confusion_cognition_sundowning": [/\b(sundown|late day confusion|evening confusion|worse (?:late|afternoon|evening))\b/i],
    "confusion_cognition_memory_decline": [/\b(memory (?:worse|declining)|forgetting more|short.term memory|can't remember)\b/i],
    "confusion_cognition_stroke_symptoms": [/\b(stroke|slurred speech|left.sided weakness|right.sided weakness|facial droop|vision loss|sudden weakness|sudden numbness|one.sided weakness|hemiparesis|aphasia|dysarthria)\b/i],
    "confusion_cognition_tIA_symptoms": [/\b(tia|transient ischemic|temporary weakness|temporary numbness|resolved weakness|went away)\b/i],
    "confusion_cognition_seizure_activity": [/\b(seizure|convuls|staring spell|loss of awareness|unconscious episode|shaking)\b/i],
    
    // Mobility/Falls
    "mobility_falls_unsteady_gait": [/\b(unsteady|wobbly|holding (?:furniture|walls)|furniture walking|wide based gait)\b/i],
    "mobility_falls_near_fall": [/\b(almost fell|near fall|caught (?:him|her|them)self|stumbled but caught)\b/i],
    "mobility_falls_fall_no_injury": [/\b(fell|fall)(?![^.]*injur)(?![^.]*hurt)(?![^.]*fracture)/i],
    "mobility_falls_fall_with_injury": [/\b(fell|fall)[^.]*(injur|hurt|fracture|bruise|cut|bleed)/i],
    "mobility_falls_fear_of_falling": [/\b(afraid to walk|fear of falling|scared to (?:walk|move)|won't walk)\b/i],
    
    // Sleep
    "sleep_difficulty_falling_asleep": [/\b(can't sleep|trouble sleeping|difficulty falling asleep|insomnia)\b/i],
    "sleep_night_waking": [/\b(waking (?:up )?(?:at night|multiple times)|up all night|night waking)\b/i],
    "sleep_day_night_reversal": [/\b(day.?night reversal|sleeping all day|awake all night|reversed sleep)\b/i],
    
    // Mood/Behavior
    "mood_behavior_apathy_withdrawal": [/\b(apathetic|withdrawn|no interest|not caring|doesn't want to)\b/i],
    "mood_behavior_agitation_aggression": [/\b(agitat|aggress|pacing|restless|combative|hitting|yelling|screaming)\b/i],
    "mood_behavior_anxiety_fear": [/\b(anxiou|fearful|worried|scared|clingy|won't be alone)\b/i],
    "mood_behavior_depressive_symptoms": [/\b(depress|tearful|hopeless|sad|giving up|self.neglect)\b/i],
    
    // Medication
    "medication_missed_doses": [/\b(missed dose|forgot (?:med|medication|pill)|didn't take|skipped dose)\b/i],
    "medication_side_effects": [/\b(side effect|adverse reaction|since (?:starting|new) (?:med|medication))\b/i],
    "medication_wrong_dose": [/\b(wrong dose|too much|too little|double dose|half dose|incorrect dose)\b/i],
    "medication_new_medication": [/\b(new (?:med|medication|prescription)|started (?:on|taking))\b/i],
    
    // Elimination
    "elimination_constipation": [/\b(constipat|no bowel|hasn't poop|no bm|3 days|no stool)\b/i],
    "elimination_incontinence_new": [/\b(new incontinence|wet (?:him|her)self|soiled|accident|incontinent)\b/i],
    "elimination_uti_signs": [/\b(uti|urinary tract|burning urine|frequency|urgency|foul urine)\b/i],
    
    // Skin Integrity
    "skin_integrity_redness_pressure": [/\b(red (?:area|spot)|pressure (?:area|spot|red)|bony prominence|reddened)\b/i],
    "skin_integrity_open_wound": [/\b(skin tear|abrasion|pressure ulcer|bedsore|open wound|wound)\b/i],
    
    // Pain
    "pain_verbal_pain": [/\b(hurts|pain|aching|sore|ouch|grimac|guarding|holding)\b/i],
    "pain_behavioral_pain": [/\b(resist(?:ance|ing) care|agitation with care|moaning|groaning|pacing)\b/i],
    
    // Social Engagement
    "social_engagement_isolation": [/\b(isolat|alone all day|no visitors|not leaving room|stays in room)\b/i],
    "social_engagement_activity_cessation": [/\b(stopped (?:going|doing|attending)|no longer|quit|gave up)\b/i],
  };
  
  const key = `${domain}_${signalType}`;
  return patternMap[key] || [];
}

/**
 * Calculate clinical risk score based on detected domains
 */
function calculateClinicalRiskScore(domains: CareDomainSignal[]): number {
  if (domains.length === 0) return 0.2;
  
  const maxSignificance = domains.reduce((max, d) => {
    const scores = { low: 0.3, medium: 0.5, high: 0.7, critical: 0.95 };
    return Math.max(max, scores[d.clinical_significance]);
  }, 0);
  
  // Boost for cascades
  const allCascades = domains.flatMap(d => d.common_cascades);
  const uniqueCascades = new Set(allCascades).size;
  const cascadeBoost = Math.min(0.15, uniqueCascades * 0.03);
  
  // Boost for multiple domains
  const multiDomainBoost = Math.min(0.1, (domains.length - 1) * 0.05);
  
  return clamp01(maxSignificance + cascadeBoost + multiDomainBoost);
}

/**
 * Calibrated magnitude scoring using care domain clinical significance
 */
function scoreMagnitudeCalibrated(context: AssessmentContext): AttentionDimensionScore {
  const domains = context.detected_domains || detectCareDomains(context);
  context.detected_domains = domains;
  
  if (domains.length === 0) {
    return {
      dimension: "magnitude",
      score: 0.3,
      evidence: `No specific care domain detected: ${context.signal}`,
      weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.magnitude,
    };
  }
  
  const maxSeverity = domains.reduce((max, d) => {
    const scores = { mild: 0.4, moderate: 0.6, severe: 0.85 };
    return Math.max(max, scores[d.baseline_deviation]);
  }, 0);
  
  const clinicalRisk = calculateClinicalRiskScore(domains);
  context.clinical_risk_score = clinicalRisk;
  
  // Combine baseline deviation severity with clinical risk
  const calibratedScore = (maxSeverity * 0.6) + (clinicalRisk * 0.4);
  
  const domainNames = domains.map(d => `${d.domain}:${d.signal_type}`).join(", ");
  
  return {
    dimension: "magnitude",
    score: clamp01(calibratedScore),
    evidence: `Care domains: ${domainNames} | Clinical risk: ${(clinicalRisk * 100).toFixed(0)}%`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.magnitude,
  };
}

/**
 * Calibrated risk scoring using care domain clinical significance
 */
function scoreRiskCalibrated(context: AssessmentContext): AttentionDimensionScore {
  const domains = context.detected_domains || detectCareDomains(context);
  
  if (domains.length === 0) {
    return {
      dimension: "risk",
      score: 0.2,
      evidence: `No specific risk domain: ${context.signal}`,
      weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.risk,
    };
  }
  
  const clinicalRisk = context.clinical_risk_score || calculateClinicalRiskScore(domains);
  
  // Check for critical cascade combinations
  let cascadeRisk = 0;
  for (const domain of domains) {
    for (const cascade of domain.common_cascades) {
      if (domains.some(d => d.domain === cascade)) {
        cascadeRisk = Math.max(cascadeRisk, 0.15);
      }
    }
  }
  
  const calibratedScore = clamp01(clinicalRisk + cascadeRisk);
  
  return {
    dimension: "risk",
    score: calibratedScore,
    evidence: `Clinical risk: ${(clinicalRisk * 100).toFixed(0)}% | Cascade risk: ${(cascadeRisk * 100).toFixed(0)}%`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.risk,
  };
}

/**
 * Calibrated functional impact based on care domain
 */
function scoreFunctionalImpactCalibrated(context: AssessmentContext): AttentionDimensionScore {
  const domains = context.detected_domains || detectCareDomains(context);
  
  if (domains.length === 0) {
    return scoreFunctionalImpact(context);
  }
  
  // High functional impact domains
  const highImpactDomains: CareDomain[] = ["mobility_falls", "elimination", "skin_integrity", "pain"];
  const mediumImpactDomains: CareDomain[] = ["appetite_nutrition", "confusion_cognition", "medication"];
  
  let maxScore = 0.2;
  for (const domain of domains) {
    if (highImpactDomains.includes(domain.domain)) {
      maxScore = Math.max(maxScore, 0.8);
    } else if (mediumImpactDomains.includes(domain.domain)) {
      maxScore = Math.max(maxScore, 0.5);
    }
  }
  
  // Boost if multiple high-impact domains
  const highImpactCount = domains.filter(d => highImpactDomains.includes(d.domain)).length;
  if (highImpactCount >= 2) maxScore = Math.min(0.95, maxScore + 0.15);
  
  return {
    dimension: "functional_impact",
    score: clamp01(maxScore),
    evidence: `Functional impact from domains: ${domains.map(d => d.domain).join(", ")}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.functional_impact,
  };
}

/**
 * Calibrated cognitive/behavioral impact
 */
function scoreCognitiveBehavioralImpactCalibrated(context: AssessmentContext): AttentionDimensionScore {
  const domains = context.detected_domains || detectCareDomains(context);
  
  if (domains.length === 0) {
    return scoreCognitiveBehavioralImpact(context);
  }
  
  const cognitiveDomains: CareDomain[] = ["confusion_cognition", "mood_behavior", "medication"];
  let maxScore = 0.1;
  
  for (const domain of domains) {
    if (cognitiveDomains.includes(domain.domain)) {
      const scores = { low: 0.3, medium: 0.5, high: 0.8, critical: 0.95 };
      maxScore = Math.max(maxScore, scores[domain.clinical_significance]);
    }
  }
  
  // Acute confusion gets highest score
  const acuteConfusion = domains.find(d => d.signal_type === "acute_confusion");
  if (acuteConfusion) maxScore = 0.95;
  
  return {
    dimension: "cognitive_behavioral_impact",
    score: clamp01(maxScore),
    evidence: `Cognitive/behavioral from: ${domains.filter(d => cognitiveDomains.includes(d.domain)).map(d => d.domain).join(", ")}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.cognitive_behavioral_impact,
  };
}

/**
 * Calibrated medication implications
 */
function scoreMedicationImplicationsCalibrated(context: AssessmentContext): AttentionDimensionScore {
  const domains = context.detected_domains || detectCareDomains(context);
  
  const medDomains = domains.filter(d => d.domain === "medication");
  if (medDomains.length === 0) {
    return scoreMedicationImplications(context);
  }
  
  let maxScore = 0.3;
  for (const domain of medDomains) {
    const scores = { low: 0.4, medium: 0.6, high: 0.85, critical: 0.95 };
    maxScore = Math.max(maxScore, scores[domain.clinical_significance]);
  }
  
  return {
    dimension: "medication_implications",
    score: clamp01(maxScore),
    evidence: `Medication issues: ${medDomains.map(d => d.signal_type).join(", ")}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.medication_implications,
  };
}

/**
 * Calibrated acuteness with care-domain awareness
 */
function scoreAcutenessCalibrated(context: AssessmentContext): AttentionDimensionScore {
  const domains = context.detected_domains || detectCareDomains(context);
  const hoursSinceOnset = context.time_since_onset_ms / 3600000;
  
  let baseScore = 0;
  if (hoursSinceOnset < 1) baseScore = 0.95;
  else if (hoursSinceOnset < 6) baseScore = 0.8;
  else if (hoursSinceOnset < 24) baseScore = 0.6;
  else if (hoursSinceOnset <= 72) baseScore = 0.4;
  else if (hoursSinceOnset <= 168) baseScore = 0.25;
  else baseScore = 0.1;
  
  // Adjust for domain-specific escalation timelines
  if (domains.length > 0) {
    const minTimeline = Math.min(...domains.map(d => d.typical_escalation_timeline_hours));
    if (hoursSinceOnset > minTimeline * 2) {
      baseScore = Math.min(0.9, baseScore + 0.2); // Persisted beyond expected timeline
    }
  }
  
  return {
    dimension: "acuteness",
    score: clamp01(baseScore),
    evidence: `Duration: ${hoursSinceOnset.toFixed(1)}h${domains.length ? ` | Expected escalation: ${Math.min(...domains.map(d => d.typical_escalation_timeline_hours))}h` : ""}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.acuteness,
  };
}

/**
 * Calibrated baseline deviation using care domain knowledge
 */
function scoreBaselineDeviationCalibrated(context: AssessmentContext): AttentionDimensionScore {
  const domains = context.detected_domains || detectCareDomains(context);
  
  if (domains.length === 0) {
    return scoreBaselineDeviation(context);
  }
  
  // If we detected specific care domains with known deviation levels
  let maxDeviation = 0;
  for (const domain of domains) {
    const scores = { mild: 0.4, moderate: 0.7, severe: 0.9 };
    maxDeviation = Math.max(maxDeviation, scores[domain.baseline_deviation]);
  }
  
  return {
    dimension: "baseline_deviation",
    score: clamp01(maxDeviation),
    evidence: `Domain-specific deviation: ${domains.map(d => `${d.domain}=${d.baseline_deviation}`).join(", ")}`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.baseline_deviation,
  };
}

/**
 * Calibrated time pressure using care domain escalation timelines
 */
function scoreTimePressureCalibrated(context: AssessmentContext): AttentionDimensionScore {
  const domains = context.detected_domains || detectCareDomains(context);
  
  if (domains.length === 0) {
    return scoreTimePressure(context);
  }
  
  const hoursSinceOnset = context.time_since_onset_ms / 3600000;
  const minTimeline = Math.min(...domains.map(d => d.typical_escalation_timeline_hours));
  
  let score = 0.2;
  if (hoursSinceOnset < minTimeline * 0.25) score = 0.9;  // Well within acute window
  else if (hoursSinceOnset < minTimeline * 0.5) score = 0.7;
  else if (hoursSinceOnset < minTimeline) score = 0.5;
  else if (hoursSinceOnset < minTimeline * 2) score = 0.6; // Past timeline - escalating
  else score = 0.8; // Significantly past expected timeline
  
  return {
    dimension: "time_pressure",
    score: clamp01(score),
    evidence: `Time pressure: ${hoursSinceOnset.toFixed(1)}h elapsed, ${minTimeline}h typical escalation window`,
    weight: DEFAULT_ATTENTION_LIFECYCLE_CONFIG.dimension_weights.time_pressure,
  };
}

const DIMENSION_SCORERS: Record<AttentionDimension, (context: AssessmentContext) => AttentionDimensionScore> = {
  magnitude: scoreMagnitude,
  acuteness: scoreAcuteness,
  risk: scoreRisk,
  baseline_deviation: scoreBaselineDeviation,
  persistence: scorePersistence,
  recurrence: scoreRecurrence,
  trend: scoreTrend,
  functional_impact: scoreFunctionalImpact,
  cognitive_behavioral_impact: scoreCognitiveBehavioralImpact,
  medication_implications: scoreMedicationImplications,
  caregiver_capacity: scoreCaregiverCapacity,
  open_loop_dependency: scoreOpenLoopDependency,
  time_pressure: scoreTimePressure,
  uncertainty: scoreUncertainty,
  evidence_quality: scoreEvidenceQuality,
  action_already_taken: scoreActionAlreadyTaken,
};

const DIMENSION_SCORERS_CALIBRATED: Record<AttentionDimension, (context: AssessmentContext) => AttentionDimensionScore> = {
  magnitude: scoreMagnitudeCalibrated,
  acuteness: scoreAcutenessCalibrated,
  risk: scoreRiskCalibrated,
  baseline_deviation: scoreBaselineDeviationCalibrated,
  persistence: scorePersistence,
  recurrence: scoreRecurrence,
  trend: scoreTrend,
  functional_impact: scoreFunctionalImpactCalibrated,
  cognitive_behavioral_impact: scoreCognitiveBehavioralImpactCalibrated,
  medication_implications: scoreMedicationImplicationsCalibrated,
  caregiver_capacity: scoreCaregiverCapacity,
  open_loop_dependency: scoreOpenLoopDependency,
  time_pressure: scoreTimePressureCalibrated,
  uncertainty: scoreUncertainty,
  evidence_quality: scoreEvidenceQuality,
  action_already_taken: scoreActionAlreadyTaken,
};

export function assessAttention(
  context: AssessmentContext,
  config: AttentionLifecycleConfig = DEFAULT_ATTENTION_LIFECYCLE_CONFIG,
  useCalibration: boolean = true
): AttentionAssessment {
  const scorers = useCalibration ? DIMENSION_SCORERS_CALIBRATED : DIMENSION_SCORERS;
  const dimensionScores: AttentionDimensionScore[] = [];
  
  for (const dimension of Object.keys(scorers) as AttentionDimension[]) {
    const scorer = scorers[dimension];
    if (scorer) {
      dimensionScores.push(scorer(context));
    }
  }

  let weightedSum = 0;
  let totalWeight = 0;
  
  for (const ds of dimensionScores) {
    weightedSum += ds.score * ds.weight;
    totalWeight += ds.weight;
  }

  const compositeScore = totalWeight > 0 ? weightedSum / totalWeight : 0;

  const recommendedState = determineAttentionState(compositeScore, context, config);
  const confidence = calculateConfidence(dimensionScores, context);
  const reasoning = generateReasoning(dimensionScores, recommendedState, context, compositeScore);

  return {
    assessment_id: `assess_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
    timestamp: new Date().toISOString(),
    dimension_scores: dimensionScores,
    composite_score: clamp01(compositeScore),
    recommended_state: recommendedState,
    confidence,
    reasoning,
  };
}

function determineAttentionState(
  compositeScore: number,
  context: AssessmentContext,
  config: AttentionLifecycleConfig
): AttentionState {
  // Check for resolution/benign phrases that should force BACKGROUND or NOT_RELEVANT
  const signal = context.signal.toLowerCase();
  const resolutionPhrases = [
    "but normal", "but had normal", "then normal", "returned to normal",
    "back to normal", "resolved", "recovered", "improved", "better now",
    "eating normally", "ate normally", "normal again", "no longer",
    "fully recovered", "back to baseline", "returned to baseline"
  ];
  const hasResolutionPhrase = resolutionPhrases.some(phrase => signal.includes(phrase));
  
  if (hasResolutionPhrase && compositeScore < config.escalation_thresholds.NEEDS_ATTENTION) {
    return "BACKGROUND";
  }
  
  // Check for treatment-started phrases that should suppress critical condition override
  const treatmentStartedPhrases = [
    "started", "began", "began taking", "on antibiotic", "on medication",
    "treatment started", "therapy started", "medication started",
    "resolved after", "treated with", "completed treatment"
  ];
  const hasTreatmentStarted = treatmentStartedPhrases.some(phrase => signal.includes(phrase));
  
  // Check for recurrence-of-resolved phrases
  const recurrencePhrases = ["again", "recurrence", "returned", "new episode", "relapse"];
  const isRecurrence = recurrencePhrases.some(phrase => signal.includes(phrase));
  
  // Check for past/resolved episode phrases
  const pastEpisodePhrases = ["resolved after", "history of", "previous", "past episode", "previously resolved", "was resolved", "had been resolved"];
  const isPastEpisode = pastEpisodePhrases.some(phrase => signal.includes(phrase));
  
  // Critical condition override - immediate escalation for life-threatening conditions
  const domains = context.detected_domains || detectCareDomains(context);
  const hasCriticalCondition = domains.some(d => 
    d.clinical_significance === "critical" && 
    ["stroke_symptoms", "seizure_activity", "dehydration_signs", "uti_signs", "fall_with_injury", "wrong_dose"].includes(d.signal_type)
  );
  
  // Suppress critical condition override if treatment has started, it's a recurrence of resolved issue, or it's a past episode
  const suppressCritical = hasCriticalCondition && (hasTreatmentStarted || isRecurrence || isPastEpisode);
  if (hasCriticalCondition && !suppressCritical) return "EMERGENCY";
  
  // Urgent condition override
  const hasUrgentCondition = domains.some(d => 
    d.clinical_significance === "critical" || 
    (d.clinical_significance === "high" && ["acute_confusion", "side_effects", "missed_doses", "new_incontinence", "open_wound", "verbal_pain", "behavioral_pain"].includes(d.signal_type))
  );
  
  // Also suppress urgent condition for treated/recurring/past issues
  const suppressUrgent = hasUrgentCondition && (hasTreatmentStarted || isRecurrence || isPastEpisode);
  
  // Suppress URGENT escalation if condition has persisted well beyond its escalation timeline
  const hoursSinceOnsetUrgent = context.time_since_onset_ms / 3600000;
  const pastEscalationTimeline = domains.some(d => 
    (d.clinical_significance === "critical" || d.clinical_significance === "high") && 
    hoursSinceOnsetUrgent > d.typical_escalation_timeline_hours * 4
  );
  
  if (hasUrgentCondition && !suppressUrgent && !pastEscalationTimeline) {
    if (compositeScore >= config.escalation_thresholds.URGENT) return "URGENT";
    if (compositeScore >= config.escalation_thresholds.HIGH_PRIORITY) return "HIGH_PRIORITY";
  }
  
  // If urgent condition has persisted beyond escalation timeline, cap at HIGH_PRIORITY
  if (hasUrgentCondition && pastEscalationTimeline) {
    if (compositeScore >= config.escalation_thresholds.HIGH_PRIORITY) return "HIGH_PRIORITY";
  }
  
  // Persistent high-significance condition override
  // If a high/critical significance domain has persisted beyond 1.5x its escalation timeline,
  // allow HIGH_PRIORITY at a slightly lower composite threshold
  const hoursSinceOnset = context.time_since_onset_ms / 3600000;
  const hasPersistentHighSignificance = domains.some(d => 
    (d.clinical_significance === "high" || d.clinical_significance === "critical") &&
    hoursSinceOnset > d.typical_escalation_timeline_hours * 1.5
  );
  
  // Don't apply persistent HIGH_PRIORITY override for past/resolving episodes
  const suppressPersistent = hasPersistentHighSignificance && (hasTreatmentStarted || isRecurrence || isPastEpisode);
  if (hasPersistentHighSignificance && !suppressPersistent && compositeScore >= config.escalation_thresholds.HIGH_PRIORITY * 0.85) return "HIGH_PRIORITY";
  
  // Apply caps for suppressed critical/urgent conditions
  let maxState: AttentionState = "EMERGENCY";
  if (suppressCritical) maxState = "HIGH_PRIORITY";
  else if (suppressUrgent) maxState = "URGENT";
  
  const stateOrder: AttentionState[] = ["NOT_RELEVANT", "BACKGROUND", "WATCH", "NEEDS_ATTENTION", "HIGH_PRIORITY", "URGENT", "EMERGENCY"];
  const maxIndex = stateOrder.indexOf(maxState);
  
  if (compositeScore >= config.escalation_thresholds.EMERGENCY) {
    return maxIndex >= stateOrder.indexOf("EMERGENCY") ? "EMERGENCY" : maxState;
  }
  if (compositeScore >= config.escalation_thresholds.URGENT) {
    return maxIndex >= stateOrder.indexOf("URGENT") ? "URGENT" : maxState;
  }
  if (compositeScore >= config.escalation_thresholds.HIGH_PRIORITY) return "HIGH_PRIORITY";
  if (compositeScore >= config.escalation_thresholds.NEEDS_ATTENTION) return "NEEDS_ATTENTION";
  if (compositeScore >= config.escalation_thresholds.WATCH) return "WATCH";
  if (compositeScore >= config.escalation_thresholds.BACKGROUND) return "BACKGROUND";
  return "NOT_RELEVANT";
}

function calculateConfidence(
  dimensionScores: AttentionDimensionScore[],
  context: AssessmentContext
): number {
  const highQualityScores = dimensionScores.filter(ds => ds.score > 0.7).length;
  const totalScores = dimensionScores.length;
  
  let confidence = highQualityScores / totalScores;
  
  confidence *= context.evidence_quality === "high" ? 1.0 : 
                context.evidence_quality === "medium" ? 0.8 : 0.6;
  
  if (context.history.length === 0) confidence *= 0.8;
  if (context.trend === "unknown") confidence *= 0.9;
  
  return clamp01(confidence);
}

function generateReasoning(
  dimensionScores: AttentionDimensionScore[],
  recommendedState: AttentionState,
  context: AssessmentContext,
  compositeScore: number
): string {
  const topDimensions = [...dimensionScores]
    .sort((a, b) => b.score - a.score)
    .slice(0, 4);

  const dimensionDescriptions = topDimensions
    .map(ds => `${ds.dimension}=${(ds.score * 100).toFixed(0)}%`)
    .join(", ");

  const stateDescription = getStateDescription(recommendedState);
  
  return `Assessment recommends ${stateDescription} (composite: ${compositeScore.toFixed(2)}). Top factors: ${dimensionDescriptions}. Signal: "${context.signal}". Baseline: "${context.baseline}".`;
}

function getStateDescription(state: AttentionState): string {
  const descriptions: Record<AttentionState, string> = {
    NOT_RELEVANT: "no attention needed",
    BACKGROUND: "background monitoring",
    WATCH: "watch - pay attention, not urgent",
    NEEDS_ATTENTION: "needs attention",
    HIGH_PRIORITY: "high priority",
    URGENT: "urgent",
    EMERGENCY: "emergency",
    AWAITING_INFORMATION: "awaiting information",
    AWAITING_DECISION: "awaiting decision",
    AWAITING_ACTION: "awaiting action",
    IN_PROGRESS: "in progress",
    AWAITING_VERIFICATION: "awaiting verification",
    RESOLVED: "resolved",
    CLOSED: "closed",
    SUPERSEDED: "superseded",
  };
  return descriptions[state] || state;
}

export function createAssessmentContext(
  signal: string,
  baseline: string,
  history: string[],
  caregiverCapacity: "high" | "medium" | "low" | "unknown",
  clinicalContext: string,
  existingItems: string[],
  timeSinceOnsetMs: number,
  recurrenceCount: number,
  trend: "worsening" | "improving" | "stable" | "unknown",
  actionTaken: boolean,
  actionDescription?: string,
  evidenceQuality: "high" | "medium" | "low" = "medium",
  openLoops: string[] = [],
  uncertaintyLevel: number = 0.5,
  careRecipientId: string = "default"
): AssessmentContext {
  return {
    care_recipient_id: careRecipientId,
    signal,
    baseline,
    history,
    caregiver_capacity: caregiverCapacity,
    clinical_context: clinicalContext,
    existing_items: existingItems,
    time_since_onset_ms: timeSinceOnsetMs,
    recurrence_count: recurrenceCount,
    trend,
    action_taken: actionTaken,
    action_description: actionDescription,
    evidence_quality: evidenceQuality,
    open_loops: openLoops,
    uncertainty_level: uncertaintyLevel,
  };
}