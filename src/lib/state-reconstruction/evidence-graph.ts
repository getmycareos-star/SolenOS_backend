/**
 * State Reconstruction — Evidence Graph Construction & Entity Resolution
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */

import {
  CONTEXT_DIMENSIONS,
  PHYSICAL_SUBDOMAINS,
  COGNITIVE_SUBDOMAINS,
  FUNCTIONAL_SUBDOMAINS,
  MEDICATION_SUBDOMAINS,
  CARE_NETWORK_SUBDOMAINS,
  OPERATIONAL_SUBDOMAINS,
} from "./contract-constants";

import type {
  CareStateDomain,
  PhysicalSubdomain,
  CognitiveSubdomain,
  FunctionalSubdomain,
  MedicationSubdomain,
  CareNetworkSubdomain,
  OperationalSubdomain,
  ClaimStatus,
  EvidenceWeight,
  UncertaintyLevel,
  ContradictionType,
  SupersessionRelation,
  ContextDimension,
  StateTemporalStatus,
  ReconstructionConfidence,
} from "./contract-constants";

import type {
  Claim,
  EvidenceNode,
  ResolvedEntity,
  EvidenceGraph,
  ReconstructionInput,
  ContextualState,
} from "./types";

import { CanonicalCareEvent } from "../situation-entry/types";

const ENTITY_TYPE_KEYWORDS: Record<string, ResolvedEntity["type"]> = {
  medication: "medication",
  medicine: "medication",
  drug: "medication",
  prescription: "medication",
  doctor: "person",
  physician: "person",
  nurse: "person",
  caregiver: "person",
  daughter: "person",
  son: "person",
  spouse: "person",
  husband: "person",
  wife: "person",
  hospital: "institution",
  clinic: "institution",
  pharmacy: "institution",
  home: "place",
  house: "place",
  facility: "place",
  walker: "object",
  wheelchair: "object",
  cane: "object",
  dementia: "condition",
  alzheimer: "condition",
  diabetes: "condition",
  hypertension: "condition",
  heart: "condition",
  failure: "condition",
};

const DOMAIN_KEYWORDS: Record<CareStateDomain, string[]> = {
  physical: ["mobility", "walk", "strength", "pain", "breath", "fall", "sleep", "appetite", "weight", "eat", "swallow", "physical", "move", "stand", "sit", "balance", "transfer"],
  cognitive: ["memory", "orient", "recognize", "language", "speak", "executive", "confus", "cognit", "think", "understand", "decide", "plan", "judgment", "attention", "fluctuat"],
  functional: ["bath", "dress", "toilet", "feed", "transfer", "medication", "finance", "cook", "transport", "shop", "clean", "laundry", "phone", "adl", "iadl", "independent", "assist", "help", "need", "supervis"],
  medication: ["medication", "medicine", "drug", "prescrib", "dose", "pill", "tablet", "adherence", "compliance", "fill", "refill", "discontinu", "stop", "start", "new med", "side effect", "interaction"],
  care_network: ["caregiver", "daughter", "son", "spouse", "family", "friend", "nurse", "aide", "agency", "provider", "doctor", "coordinat", "handoff", "schedule", "availab", "responsib", "manage", "handle"],
  operational: ["appoint", "referral", "result", "test", "lab", "decision", "question", "unresolved", "open", "loop", "follow", "pending", "wait", "schedul", "cancel"],
};

const SUBDOMAIN_KEYWORDS: Record<string, string[]> = {
  mobility: ["walk", "mobil", "gait", "ambul", "stand", "balance", "transfer", "fall", "walker", "cane", "wheelchair"],
  strength: ["strength", "weak", "strong", "muscle", "power", "force", "grip"],
  pain: ["pain", "ache", "hurt", "sore", "discomfort", "analgesic"],
  breathing: ["breath", "respirat", "oxygen", "sat", "dyspnea", "short of breath", "sob", "cough", "wheez"],
  falls: ["fall", "fell", "fallen", "stumble", "trip", "near fall"],
  sleep: ["sleep", "nap", "insomnia", "rest", "awake", "night", "drows"],
  appetite: ["appetite", "hunger", "eat", "food", "meal", "intake", "nutrit"],
  weight: ["weight", "weigh", "loss", "gain", "bmi", "pound", "kg", "kilogram"],
  memory: ["memory", "remember", "forget", "recall", "retain", "short term", "long term"],
  orientation: ["orient", "time", "place", "person", "date", "day", "year", "where", "who"],
  recognition: ["recognize", "know", "identif", "familiar", "stranger", "name"],
  language: ["speak", "talk", "word", "language", "aphasia", "communicat", "understand", "convers"],
  executive_function: ["executive", "plan", "organize", "decide", "judgment", "problem", "solve", "initiat", "sequenc"],
  fluctuations: ["fluctuat", "vary", "come and go", "intermittent", "on and off", "sometimes", "episod"],
  bathing: ["bath", "shower", "wash", "hygiene", "groom"],
  dressing: ["dress", "clothe", "undress", "shoe", "sock", "button", "zipper"],
  toileting: ["toilet", "bathroom", "incontinent", "continent", "void", "bowel", "bladder", "accident"],
  feeding: ["feed", "eat", "swallow", "utensil", "cup", "drink", "choke", "aspirat"],
  transfers: ["transfer", "move", "bed", "chair", "stand up", "sit down", "pivot", "lift"],
  medication_management: ["medication", "pill", "dose", "prescrib", "organize", "remind", "box", "dispens"],
  finances: ["financ", "money", "bill", "bank", "pay", "budget", "check"],
  cooking: ["cook", "meal", "prepare", "stove", "oven", "microwave", "recipe", "kitchen"],
  transportation: ["transport", "drive", "ride", "bus", "car", "appointment", "travel"],
  prescribed: ["prescrib", "order", "doctor", "physician", "rx", "script"],
  filled: ["fill", "pharmacy", "pick up", "ready", "dispensed"],
  available: ["availab", "have", "on hand", "supply", "stock", "refill"],
  administered: ["administer", "give", "took", "taken", "dose", "time", "schedule"],
  taken: ["took", "taken", "swallow", "ingest", "compliance", "adherence"],
  discontinued: ["discontinu", "stop", "cease", "end", "halt", "no longer", "off"],
  adherence_uncertainty: ["unsure", "unknown", "maybe", "think", "not sure", "unclear", "missed", "skip", "forgot"],
  providers: ["provider", "doctor", "nurse", "therapist", "specialist", "primary", "pcp"],
  task_owners: ["manage", "handle", "responsib", "owner", "lead", "coordinat", "in charge"],
  handoffs: ["handoff", "handover", "transition", "transfer", "shift", "relief", "cover"],
  availability: ["availab", "free", "busy", "schedule", "time", "can", "cannot", "unable"],
  coordination_state: ["coordinat", "communicat", "share", "update", "inform", "align", "sync"],
  appointments: ["appoint", "visit", "see", "schedule", "book", "slot", "time"],
  referrals: ["refer", "referral", "specialist", "send to", "order"],
  pending_results: ["result", "pending", "wait", "lab", "test", "report", "outcome", "finding"],
  decisions: ["decide", "decision", "choose", "option", "plan", "agree", "consent"],
  unresolved_questions: ["question", "unclear", "unknown", "uncertain", "ask", "wonder", "clarify"],
  open_loops: ["open", "loop", "follow", "pending", "unresolved", "waiting", "outstanding"],
};

/**
 * Extract claims from a canonical care event
 */
export function extractClaimsFromEvent(
  event: CanonicalCareEvent,
  domainDefinitions: DomainDefinition[]
): Claim[] {
  const claims: Claim[] = [];
  const eventTime = event.event_time;
  const timestamp = event.timestamp;
  const ingestionTime = event.ingestion_time;

  // Extract claims from raw_input using domain definitions
  for (const domainDef of domainDefinitions) {
    const extracted = extractClaimsForDomain(event, domainDef);
    claims.push(...extracted);
  }

  // Also extract from structured attributes
  for (const [key, value] of Object.entries(event.attributes)) {
    if (value && typeof value === "string") {
      const domain = inferDomainFromText(key + " " + value);
      if (domain) {
        const subdomain = inferSubdomainFromText(domain, key + " " + value);
        if (subdomain) {
          claims.push(
            createClaim({
              domain,
              subdomain,
              statement: `${key}: ${value}`,
              event,
              context: extractContextFromEvent(event),
              evidenceWeight: inferEvidenceWeight(event),
            })
          );
        }
      }
    }
  }

  return claims;
}

/**
 * Domain definition for claim extraction
 */
export type DomainDefinition = {
  domain: CareStateDomain;
  subdomains: string[];
  keywords: string[];
  context_dimensions: string[];
  evidence_weight_default: EvidenceWeight;
};

/**
 * Create default domain definitions
 */
export function createDefaultDomainDefinitions(): DomainDefinition[] {
  return [
    {
      domain: "physical",
      subdomains: [...PHYSICAL_SUBDOMAINS],
      keywords: DOMAIN_KEYWORDS.physical,
      context_dimensions: ["location", "activity", "time_of_day", "assistive_device"],
      evidence_weight_default: "clinical_assessment",
    },
    {
      domain: "cognitive",
      subdomains: [...COGNITIVE_SUBDOMAINS],
      keywords: DOMAIN_KEYWORDS.cognitive,
      context_dimensions: ["time_of_day", "activity", "caregiver_present"],
      evidence_weight_default: "clinical_assessment",
    },
    {
      domain: "functional",
      subdomains: [...FUNCTIONAL_SUBDOMAINS],
      keywords: DOMAIN_KEYWORDS.functional,
      context_dimensions: ["location", "activity", "assistive_device", "caregiver_present"],
      evidence_weight_default: "caregiver_observation",
    },
    {
      domain: "medication",
      subdomains: [...MEDICATION_SUBDOMAINS],
      keywords: DOMAIN_KEYWORDS.medication,
      context_dimensions: ["time_of_day", "activity"],
      evidence_weight_default: "clinical_assessment",
    },
    {
      domain: "care_network",
      subdomains: [...CARE_NETWORK_SUBDOMAINS],
      keywords: DOMAIN_KEYWORDS.care_network,
      context_dimensions: ["caregiver_present", "location"],
      evidence_weight_default: "caregiver_observation",
    },
    {
      domain: "operational",
      subdomains: [...OPERATIONAL_SUBDOMAINS],
      keywords: DOMAIN_KEYWORDS.operational,
      context_dimensions: ["time_of_day", "location"],
      evidence_weight_default: "historical_record",
    },
  ];
}

/**
 * Extract claims for a specific domain
 */
function extractClaimsForDomain(
  event: CanonicalCareEvent,
  domainDef: DomainDefinition
): Claim[] {
  const claims: Claim[] = [];
  const text = event.raw_input.toLowerCase();

  // Check if event is relevant to this domain
  const hasKeyword = domainDef.keywords.some((k) => text.includes(k.toLowerCase()));
  if (!hasKeyword) return claims;

  // Extract subdomain-specific claims
  for (const subdomain of domainDef.subdomains) {
    const subKeywords = SUBDOMAIN_KEYWORDS[subdomain] || [subdomain];
    const hasSubKeyword = subKeywords.some((k) => text.includes(k.toLowerCase()));

    if (hasSubKeyword) {
      const claim = createClaim({
        domain: domainDef.domain,
        subdomain,
        statement: event.raw_input,
        event,
        context: extractContextFromEvent(event),
        evidenceWeight: domainDef.evidence_weight_default,
      });
      claims.push(claim);
    }
  }

  return claims;
}

/**
 * Create a claim with proper metadata
 */
function createClaim(params: {
  domain: CareStateDomain;
  subdomain: string;
  statement: string;
  event: CanonicalCareEvent;
  context: Record<ContextDimension, string | null>;
  evidenceWeight: EvidenceWeight;
}): Claim {
  const eventTime = params.event.event_time;
  const now = new Date().toISOString();

  return {
    id: `claim_${params.event.id}_${params.subdomain}_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    domain: params.domain,
    subdomain: params.subdomain,
    statement: params.statement,
    context: params.context,
    temporal_status: inferTemporalStatus(params.event),
    valid_from: eventTime.start || params.event.timestamp,
    valid_until: eventTime.end || null,
    superseded_by: null,
    supersession_relation: null,
    source_event_ids: [params.event.id],
    evidence_weight: params.evidenceWeight,
    uncertainty: inferUncertaintyFromEvent(params.event),
    uncertainty_reason: inferUncertaintyReason(params.event),
    confidence: inferReconstructionConfidence(params.event),
    provenance_chain: [params.event.id],
    status: "active",
  };
}

/**
 * Infer domain from text
 */
function inferDomainFromText(text: string): CareStateDomain | null {
  const lower = text.toLowerCase();
  for (const [domain, keywords] of Object.entries(DOMAIN_KEYWORDS)) {
    if (keywords.some((k) => lower.includes(k.toLowerCase()))) {
      return domain as CareStateDomain;
    }
  }
  return null;
}

/**
 * Infer subdomain from text
 */
function inferSubdomainFromText(domain: CareStateDomain, text: string): string | null {
  const lower = text.toLowerCase();
  const subdomains = {
    physical: PHYSICAL_SUBDOMAINS,
    cognitive: COGNITIVE_SUBDOMAINS,
    functional: FUNCTIONAL_SUBDOMAINS,
    medication: MEDICATION_SUBDOMAINS,
    care_network: CARE_NETWORK_SUBDOMAINS,
    operational: OPERATIONAL_SUBDOMAINS,
  }[domain];

  for (const subdomain of subdomains) {
    const keywords = SUBDOMAIN_KEYWORDS[subdomain] || [subdomain];
    if (keywords.some((k) => lower.includes(k.toLowerCase()))) {
      return subdomain;
    }
  }
  return subdomains[0] || null;
}

/**
 * Extract context from event
 */
function extractContextFromEvent(event: CanonicalCareEvent): Record<ContextDimension, string | null> {
  const context: Record<ContextDimension, string | null> = {
    location: null,
    activity: null,
    time_of_day: null,
    caregiver_present: null,
    assistive_device: null,
    social_setting: null,
  };

  const text = event.raw_input.toLowerCase();

  // Location
  if (text.includes("home") || text.includes("house")) context.location = "home";
  else if (text.includes("hospital")) context.location = "hospital";
  else if (text.includes("clinic")) context.location = "clinic";
  else if (text.includes("facility")) context.location = "facility";
  else if (text.includes("outside") || text.includes("outdoors")) context.location = "outdoors";
  else if (text.includes("indoors") || text.includes("inside")) context.location = "indoors";
  else if (text.includes("stairs") || text.includes("stair")) context.location = "stairs";

  // Activity
  if (text.includes("walk") || text.includes("mobil")) context.activity = "walking";
  else if (text.includes("bath") || text.includes("shower")) context.activity = "bathing";
  else if (text.includes("dress")) context.activity = "dressing";
  else if (text.includes("eat") || text.includes("meal")) context.activity = "eating";
  else if (text.includes("sleep") || text.includes("nap")) context.activity = "sleeping";
  else if (text.includes("toilet")) context.activity = "toileting";

  // Time of day
  if (text.includes("morning")) context.time_of_day = "morning";
  else if (text.includes("afternoon")) context.time_of_day = "afternoon";
  else if (text.includes("evening") || text.includes("night")) context.time_of_day = "evening";

  // Caregiver present
  if (text.includes("daughter") || text.includes("son") || text.includes("caregiver") || text.includes("aide") || text.includes("nurse")) {
    context.caregiver_present = "yes";
  } else if (text.includes("alone") || text.includes("by myself") || text.includes("independent")) {
    context.caregiver_present = "no";
  }

  // Assistive device
  if (text.includes("walker")) context.assistive_device = "walker";
  else if (text.includes("wheelchair")) context.assistive_device = "wheelchair";
  else if (text.includes("cane")) context.assistive_device = "cane";

  // Social setting
  if (text.includes("family") || text.includes("friend") || text.includes("visitor")) context.social_setting = "social";
  else if (text.includes("alone")) context.social_setting = "alone";

  return context;
}

/**
 * Infer temporal status from event
 */
function inferTemporalStatus(event: CanonicalCareEvent): StateTemporalStatus {
  const eventTime = event.event_time;
  const now = new Date();
  const eventDate = new Date(eventTime.start || event.timestamp);
  const diffDays = (now.getTime() - eventDate.getTime()) / (1000 * 60 * 60 * 24);

  if (diffDays <= 7) return "current";
  if (diffDays <= 30) return "recent";
  if (diffDays <= 90) return "transitional";
  return "historical";
}

/**
 * Infer uncertainty from event
 */
function inferUncertaintyFromEvent(event: CanonicalCareEvent): UncertaintyLevel {
  if (event.uncertainty.length > 0) return "high";

  const text = event.raw_input.toLowerCase();
  if (text.includes("unsure") || text.includes("unknown") || text.includes("unclear") ||
      text.includes("maybe") || text.includes("think") || text.includes("possible") ||
      text.includes("uncertain") || text.includes("?") || text.includes("provisional")) {
    return "medium";
  }

  if (event.source === "document") return "low";
  return "medium";
}

/**
 * Infer uncertainty reason
 */
function inferUncertaintyReason(event: CanonicalCareEvent): string | null {
  if (event.uncertainty.length > 0) return event.uncertainty.join("; ");

  const text = event.raw_input.toLowerCase();
  if (text.includes("unsure") || text.includes("unknown") || text.includes("unclear")) return "explicit_uncertainty";
  if (text.includes("maybe") || text.includes("think") || text.includes("possible")) return "hedged_language";
  if (text.includes("?")) return "question_format";
  if (event.status === "provisional") return "provisional_status";

  return null;
}

/**
 * Infer evidence weight
 */
function inferEvidenceWeight(event: CanonicalCareEvent): EvidenceWeight {
  if (event.source === "document") {
    const text = event.raw_input.toLowerCase();
    if (text.includes("assessment") || text.includes("evaluation") || text.includes("note")) {
      return "clinical_assessment";
    }
    return "historical_record";
  }

  const text = event.raw_input.toLowerCase();
  if (text.includes("i saw") || text.includes("i observed") || text.includes("i noticed")) {
    return "caregiver_observation";
  }
  if (text.includes("he said") || text.includes("she said") || text.includes("patient says") || text.includes("i feel")) {
    return "patient_self_report";
  }

  return "caregiver_observation";
}

/**
 * Infer reconstruction confidence
 */
function inferReconstructionConfidence(event: CanonicalCareEvent): ReconstructionConfidence {
  const weight = inferEvidenceWeight(event);
  const uncertainty = inferUncertaintyFromEvent(event);

  if (weight === "clinical_assessment" && uncertainty === "low") return "well_supported";
  if (weight === "clinical_assessment" && uncertainty === "medium") return "moderately_supported";
  if (weight === "caregiver_observation" && uncertainty === "low") return "moderately_supported";
  if (uncertainty === "high") return "weakly_supported";

  return "moderately_supported";
}

/**
 * Resolve entities across events
 */
export function resolveEntities(
  events: CanonicalCareEvent[],
  existingEntities: ResolvedEntity[] = []
): ResolvedEntity[] {
  const entityMap = new Map<string, ResolvedEntity>();

  // Load existing entities
  for (const entity of existingEntities) {
    entityMap.set(entity.canonical_name.toLowerCase(), entity);
  }

  // Extract entities from events
  for (const event of events) {
    const entities = extractEntitiesFromText(event.raw_input, event.id);
    for (const entity of entities) {
      const key = entity.canonical_name.toLowerCase();
      const existing = entityMap.get(key);

      if (existing) {
        // Merge
        existing.aliases = [...new Set([...existing.aliases, ...entity.aliases])];
        existing.source_event_ids = [...new Set([...existing.source_event_ids, ...entity.source_event_ids])];
        existing.confidence = Math.max(existing.confidence, entity.confidence);
      } else {
        entityMap.set(key, entity);
      }
    }

    // Also extract from structured entities
    for (const e of event.entities) {
      const key = e.label.toLowerCase();
      const existing = entityMap.get(key);
      const entity: ResolvedEntity = {
        id: `entity_${key.replace(/\s+/g, "_")}_${Date.now()}`,
        canonical_name: e.label,
        aliases: [e.label],
        type: e.kind,
        confidence: 0.8,
        source_event_ids: [event.id],
      };

      if (existing) {
        existing.aliases = [...new Set([...existing.aliases, e.label])];
        existing.source_event_ids = [...new Set([...existing.source_event_ids, event.id])];
        existing.confidence = Math.max(existing.confidence, 0.8);
      } else {
        entityMap.set(key, entity);
      }
    }
  }

  return Array.from(entityMap.values());
}

/**
 * Extract entities from free text
 */
function extractEntitiesFromText(text: string, eventId: string): ResolvedEntity[] {
  const entities: ResolvedEntity[] = [];
  const lower = text.toLowerCase();
  const words = lower.split(/\s+/);

  // Known entity types
  for (const [keyword, type] of Object.entries(ENTITY_TYPE_KEYWORDS)) {
    if (lower.includes(keyword)) {
      // Find the actual phrase
      const idx = lower.indexOf(keyword);
      const start = Math.max(0, idx - 20);
      const end = Math.min(lower.length, idx + keyword.length + 20);
      const context = text.slice(start, end).trim();

      entities.push({
        id: `entity_${keyword}_${eventId}_${Date.now()}`,
        canonical_name: keyword.charAt(0).toUpperCase() + keyword.slice(1),
        aliases: [keyword, context],
        type,
        confidence: 0.6,
        source_event_ids: [eventId],
      });
    }
  }

  // Named entities (capitalized words)
  const namedEntities = text.match(/\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b/g) || [];
  for (const name of namedEntities) {
    if (name.length > 2 && !ENTITY_TYPE_KEYWORDS[name.toLowerCase()]) {
      entities.push({
        id: `entity_${name.replace(/\s+/g, "_")}_${eventId}_${Date.now()}`,
        canonical_name: name,
        aliases: [name],
        type: "person",
        confidence: 0.5,
        source_event_ids: [eventId],
      });
    }
  }

  return entities;
}

/**
 * Build evidence graph from events
 */
export function buildEvidenceGraph(input: ReconstructionInput): EvidenceGraph {
  const domainDefinitions = createDefaultDomainDefinitions();

  // Extract all claims
  const allClaims: Claim[] = [];
  for (const event of input.events) {
    const claims = extractClaimsFromEvent(event, domainDefinitions);
    allClaims.push(...claims);
  }

  // Resolve entities
  const entities = resolveEntities(input.events);

  // Build nodes
  const nodes: EvidenceNode[] = [];
  const claimIndex = new Map<string, Claim>();

  for (const event of input.events) {
    const eventClaims = allClaims.filter((c) => c.source_event_ids.includes(event.id));
    for (const claim of eventClaims) {
      claimIndex.set(claim.id, claim);
    }

    nodes.push({
      id: `node_${event.id}`,
      event_id: event.id,
      event_type: event.extracted_type,
      timestamp: event.timestamp,
      ingestion_time: event.ingestion_time,
      claims: eventClaims,
      entities: entities.filter((e) => e.source_event_ids.includes(event.id)),
      supersedes: [],
      superseded_by: [],
      contradictions: [],
    });
  }

  return {
    care_recipient_id: input.care_recipient_id,
    nodes,
    entities,
    claim_index: claimIndex,
    supersession_chains: [],
    contradiction_sets: [],
  };
}