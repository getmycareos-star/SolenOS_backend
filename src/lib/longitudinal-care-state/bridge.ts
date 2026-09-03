import type { CanonicalCareEvent } from "../situation-entry/types";
import type { CareStateAssertion } from "./types";
import { eventToAssertions } from "./event-adapter";

/**
 * STATE ASSERTION BRIDGE
 *
 * Bridges between the legacy TrackedSituation model and the new
 * Longitudinal Care State primitive.
 *
 * BREAK POINTS ADDRESSED:
 * 1. TrackedSituation had no temporal validity — now maps to time-bounded assertions
 * 2. StateSituation was a flat summary — now derived from current assertions
 * 3. Resolution engine lifecycle transitions must expire corresponding assertions
 * 4. Demand engine must link to state dimensions, not just situations
 *
 * This bridge ensures backward compatibility while the system migrates
 * to the new primitive.
 */

export function trackedSituationToAssertions(
  situation: {
    id: string;
    title: string;
    status: string;
    careSessionId?: string;
    userId?: string;
    createdAt: string;
    updatedAt: string;
    resolvedAt?: string;
    archivedAt?: string;
    documentIds: string[];
    timelineEntryIds: string[];
  },
): CareStateAssertion[] {
  const assertions: CareStateAssertion[] = [];
  const now = new Date().toISOString();

  const validityStart = situation.createdAt;
  const normalizedStatus = situation.status.toUpperCase();
  const validityEnd =
    normalizedStatus === "RESOLVED" || normalizedStatus === "ARCHIVED"
      ? situation.resolvedAt ?? situation.archivedAt ?? situation.updatedAt
      : null;

  const statusMap: Record<string, CareStateAssertion["status"]> = {
    ACTIVE: "active",
    RESOLVED: "resolved",
    ARCHIVED: "resolved",
  };

  assertions.push({
    id: `assertion-situation-${situation.id}`,
    dimension: "active_conditions",
    value: situation.title.slice(0, 500),
    status: statusMap[situation.status] ?? "unknown",
    validity_start: validityStart,
    validity_end: validityEnd,
    confidence: 0.5,
    evidence_ids: [...situation.documentIds, ...situation.timelineEntryIds],
    event_ids: situation.timelineEntryIds,
    conflict_status: "coexisting",
    provenance_note: `Derived from TrackedSituation ${situation.id}`,
    created_at: situation.createdAt,
    updated_at: situation.updatedAt,
    care_recipient_id: situation.careSessionId ?? "default",
    caregiver_id: situation.userId,
  });

  return assertions;
}

export function stateAssertionToUiSituation(assertion: CareStateAssertion): {
  id: string;
  title: string;
  status: string;
  updatedAt: string;
} {
  const statusMap: Record<string, string> = {
    active: "ACTIVE",
    resolved: "RESOLVED",
    suspended: "ARCHIVED",
    unknown: "ACTIVE",
  };

  return {
    id: assertion.id,
    title: assertion.value,
    status: statusMap[assertion.status] ?? "ACTIVE",
    updatedAt: assertion.updated_at,
  };
}

export function eventsToAssertions(events: CanonicalCareEvent[]): CareStateAssertion[] {
  return events.flatMap((event) => eventToAssertions(event));
}

export function expireAssertionsForResolution(
  assertions: CareStateAssertion[],
  situationId: string,
  resolvedAt: string,
): CareStateAssertion[] {
  return assertions.map((a) => {
    if (a.event_ids.includes(situationId) && a.validity_end === null) {
      return {
        ...a,
        validity_end: resolvedAt,
        status: "resolved" as const,
        updated_at: resolvedAt,
      };
    }
    return a;
  });
}

export function supersedeAssertionsForNewSituation(
  assertions: CareStateAssertion[],
  priorSituationId: string,
  newSituationId: string,
  supersededAt: string,
): CareStateAssertion[] {
  return assertions.map((a) => {
    if (a.event_ids.includes(priorSituationId) && a.validity_end === null) {
      return {
        ...a,
        validity_end: supersededAt,
        status: "resolved" as const,
        superseded_by_id: newSituationId,
        updated_at: supersededAt,
      };
    }
    return a;
  });
}
