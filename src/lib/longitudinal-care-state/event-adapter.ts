import type {
  CanonicalCareEvent,
  CareStateAssertion,
  CareStateDimension,
  CARE_STATE_DIMENSIONS,
  CARE_STATE_STATUSES,
  TRANSITION_MECHANISMS,
  TransitionMechanism,
} from "./types";

/**
 * STATE ASSERTION ADAPTER
 *
 * Converts CanonicalCareEvent into CareStateAssertion(s).
 *
 * BREAK: Previously, events were stored as flat records with no temporal validity.
 * NOW: Each event that represents a care state dimension produces a time-bounded
 * assertion with explicit validity period, confidence, and evidence links.
 *
 * ONE EVENT → ONE OR MORE ASSERTIONS
 *
 * Mapping rules:
 * - extracted_type determines the primary dimension
 * - attributes may produce additional dimension assertions
 * - status determines assertion status
 * - event_time provides validity_start
 * - ingestion_time provides created_at
 */

export function eventToAssertions(event: CanonicalCareEvent): CareStateAssertion[] {
  const assertions: CareStateAssertion[] = [];
  const now = new Date().toISOString();
  const validityStart = event.event_time?.start ?? event.ingestion_time ?? now;
  const confidence = computeAssertionConfidence(event);
  const mechanism = inferMechanism(event);

  const primaryDimension = mapExtractedTypeToDimension(event.extracted_type);
  if (primaryDimension) {
    assertions.push(createAssertion(event, primaryDimension, confidence, validityStart, mechanism));
  }

  for (const [attrKey, attrValue] of Object.entries(event.attributes)) {
    if (attrKey === "source_situation_text") continue;
    const dimension = mapAttributeToDimension(attrKey, attrValue);
    if (dimension && !assertions.some((a) => a.dimension === dimension)) {
      assertions.push(createAssertion(event, dimension, confidence, validityStart, mechanism));
    }
  }

  return assertions;
}

function createAssertion(
  event: CanonicalCareEvent,
  dimension: CareStateDimension,
  confidence: number,
  validityStart: string,
  mechanism: TransitionMechanism,
): CareStateAssertion {
  const value = extractValueForDimension(event, dimension);
  const status = mapEventStatusToAssertionStatus(event.status);

  return {
    id: `assertion-${event.id}-${dimension}`,
    dimension,
    value,
    status,
    validity_start: validityStart,
    validity_end: null,
    confidence,
    evidence_ids: [event.id],
    event_ids: [event.id],
    conflict_status: "coexisting",
    provenance_note: `Derived from ${event.extracted_type} event ${event.id} via ${mechanism}`,
    created_at: event.ingestion_time,
    updated_at: new Date().toISOString(),
    care_recipient_id: event.attributes.care_recipient_id as string,
    caregiver_id: event.attributes.caregiver_id as string,
  };
}

function computeAssertionConfidence(event: CanonicalCareEvent): number {
  let confidence = 0.5;

  if (event.status === "committed") {
    confidence += 0.3;
  } else if (event.status === "provisional") {
    confidence -= 0.2;
  } else if (event.status === "unparsed_raw") {
    confidence -= 0.3;
  }

  if (event.integrity?.field_confidence?.extracted_fact?.user_confirmed) {
    confidence += 0.2;
  }

  if (event.uncertainty && event.uncertainty.length > 0) {
    confidence -= 0.1 * event.uncertainty.length;
  }

  return Math.max(0, Math.min(1, confidence));
}

function inferMechanism(event: CanonicalCareEvent): TransitionMechanism {
  if (event.source === "document") {
    return "new_evidence";
  }
  if (event.attributes.entry_method === "voice") {
    return "clinical_event";
  }
  return "new_evidence";
}

function mapEventStatusToAssertionStatus(
  status: CanonicalCareEvent["status"],
): CareStateAssertion["status"] {
  switch (status) {
    case "committed":
      return "active";
    case "provisional":
      return "active";
    case "unparsed_raw":
      return "unknown";
    case "invalidated":
      return "resolved";
    case "superseded":
      return "resolved";
    default:
      return "unknown";
  }
}

function mapExtractedTypeToDimension(
  extractedType: string,
): CareStateDimension | null {
  const mapping: Record<string, CareStateDimension> = {
    condition: "active_conditions",
    symptom: "symptoms",
    medication: "medications",
    allergy: "allergies",
    treatment: "treatments",
    procedure: "procedures",
    functional_status: "functional_status",
    cognitive_status: "cognitive_status",
    mobility: "mobility",
    living_situation: "living_situation",
    provider: "providers",
    care_dependency: "care_dependencies",
    risk: "risks",
    restriction: "restrictions",
    goal: "goals",
    pending: "pending_situations",
  };
  return mapping[extractedType] ?? null;
}

function mapAttributeToDimension(
  key: string,
  value: unknown,
): CareStateDimension | null {
  if (key.includes("mobility") || key.includes("walk")) return "mobility";
  if (key.includes("cognitive") || key.includes("memory") || key.includes("confusion")) return "cognitive_status";
  if (key.includes("functional") || key.includes("adl") || key.includes("bathing")) return "functional_status";
  if (key.includes("living") || key.includes("home") || key.includes("facility")) return "living_situation";
  if (key.includes("medication") || key.includes("dose") || key.includes("pill")) return "medications";
  if (key.includes("allergy") || key.includes("reaction")) return "allergies";
  if (key.includes("condition") || key.includes("diagnosis") || key.includes("disease")) return "active_conditions";
  if (key.includes("symptom") || key.includes("pain") || key.includes("fever")) return "symptoms";
  if (key.includes("provider") || key.includes("doctor") || key.includes("nurse")) return "providers";
  if (key.includes("risk") || key.includes("fall") || key.includes("wound")) return "risks";
  if (key.includes("care_dep") || key.includes("dependency")) return "care_dependencies";
  return null;
}

function extractValueForDimension(event: CanonicalCareEvent, dimension: CareStateDimension): string {
  const attrValue = event.attributes[dimension];
  if (typeof attrValue === "string" && attrValue.trim()) {
    return attrValue.trim().slice(0, 500);
  }
  if (typeof attrValue === "boolean") {
    return attrValue ? "true" : "false";
  }
  if (Array.isArray(attrValue) && attrValue.length > 0) {
    return String(attrValue[0]).slice(0, 500);
  }
  const raw = event.raw_input?.trim() ?? "";
  return raw.slice(0, 500) || `${event.extracted_type}`;
}
