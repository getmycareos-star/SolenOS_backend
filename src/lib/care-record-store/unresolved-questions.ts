export interface UnresolvedQuestion {
  id: string;
  careRecipientId: string;
  question: string;
  sourceEventId?: string | null;
  status: "open" | "resolved" | "invalidated";
  resolvedAt?: string | null;
  resolutionEventId?: string | null;
  createdAt: string;
  updatedAt: string;
}
