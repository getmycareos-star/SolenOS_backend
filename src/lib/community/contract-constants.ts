/** Community feature — contract constants. */

export const COMMUNITY_AUTHOR_TYPES = [
  "peer_caregiver",
  "solenos_verified",
  "professional",
  "professional_pending",
] as const;

export const COMMUNITY_VISIBILITY = [
  "public",
  "circle",
  "anonymous",
] as const;

export const COMMUNITY_POST_STATUSES = [
  "pending_moderation",
  "approved",
  "flagged",
  "archived",
] as const;

export const REACTION_TYPES = [
  "like",
  "thank_you",
  "support",
  "caregiver_answer",
  "caregiver_helpful",
] as const;

export const COMMUNITY_POST_TYPES = [
  "experience",
  "question",
  "announcement",
] as const;

export const COMMUNITY_CARE_STAGES = [
  "early",
  "moderate",
  "late",
  "unspecified",
] as const;

export const COMMUNITY_TAGS = [
  "sundowning",
  "wandering",
  "medication",
  "behavior",
  "sleep",
  "communication",
  "mobility",
  "nutrition",
  "hydration",
  "activities",
  "safety",
  "routine",
  "appointments",
  "general",
] as const;

export const COMMUNITY_IDENTITY =
  "Shared experience, not medical advice — always confirm with a clinician.";

export const DISTRESS_PATTERNS: readonly RegExp[] = [
  /\b(i can'?t cope|burnout|overwhelmed|exhausted|can't do this|need help)\b/i,
  /\b(depressed|suicid|harming myself|end it all)\b/i,
  /\b(no support|alone|no one helps|giving up)\b/i,
];

export const RISKY_ADVICE_PATTERNS: readonly { pattern: RegExp; label: string }[] = [
  { pattern: /\b(stop|discontinue|skip|cut)\s+(?:the\s+)?meds?\b/i, label: "medication_discontinuation" },
  { pattern: /\b(don't take|against doctor|doctor is wrong)\b/i, label: "anti_medical_advice" },
  { pattern: /\b(homeopathic|natural cure|alternative remedy)\s+for\s+(?:alzheimers?|dementia)\b/i, label: "unproven_treatment" },
];

export const DEFAULT_RELEVANCE_TTL_HOURS = 90;

export const COMMUNITY_STORE_SCHEMA = {
  community_posts: {
    table: "community_posts",
    columns: [
      "id",
      "caregiver_id",
      "author_type",
      "author_label",
      "author_avatar_seed",
      "title",
      "body",
      "media",
      "visibility",
      "care_stage",
      "tags",
      "status",
      "moderation_reason",
      "relevance_expires_at",
      "created_at",
      "updated_at",
      "provenance_source",
    ] as const,
  },
  community_reactions: {
    table: "community_reactions",
    columns: [
      "id",
      "post_id",
      "caregiver_id",
      "author_type",
      "reaction_type",
      "created_at",
    ] as const,
  },
} as const;
