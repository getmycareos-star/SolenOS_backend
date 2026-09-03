import type { CanonicalCareEvent, CareContextRoot } from "../situation-entry/types";
import type { MemoryLayerStore } from "../care-memory-layers/types";
import type { UnresolvedQuestion } from "../care-record-store/unresolved-questions";

export interface CareRecordStore {
  getCareContextRoot(careRecipientId: string): Promise<CareContextRoot | null>;
  appendEvents(careRecipientId: string, events: CanonicalCareEvent[]): Promise<CareContextRoot>;
  invalidateEvent(careRecipientId: string, eventId: string, reason?: string): Promise<CareContextRoot | null>;
  applyUserCorrection(
    careRecipientId: string,
    eventId: string,
    fields: Record<string, unknown>,
    reason?: string,
  ): Promise<CareContextRoot | null>;
  supersedeEvent(
    careRecipientId: string,
    originalEventId: string,
    replacement: CanonicalCareEvent,
    reason?: string,
  ): Promise<{ superseded: CanonicalCareEvent; active: CanonicalCareEvent } | null>;

  getMemoryLayers(careRecipientId: string): Promise<MemoryLayerStore | null>;
  persistMemoryLayers(careRecipientId: string, layers: MemoryLayerStore): Promise<void>;

  getUnresolvedQuestions(careRecipientId: string): Promise<UnresolvedQuestion[]>;
  persistUnresolvedQuestions(careRecipientId: string, questions: UnresolvedQuestion[]): Promise<void>;
  resolveQuestion(careRecipientId: string, questionId: string, resolutionEventId?: string): Promise<void>;
}
