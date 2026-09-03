import type {
  EvidenceItem,
  EvidenceSource,
  EvidenceKind,
  Claim,
  ClaimId,
  EvidenceId,
  Inference,
  InferenceId,
  DerivationStep,
  ContradictionRecord,
  KnowledgeRevisionEntry,
  UnknownState,
  AssumptionRecord,
  EpistemicViolationRecord,
  EpistemicViolation,
  EpistemicRule,
  EpistemicStatus,
  ClaimLifecycleStatus,
  UncertaintyLevel,
  AbsenceModel,
  ReasoningType,
  InferenceConfidence,
  InferenceInput,
  ClaimProvenance,
  EpistemicFoundationQuery,
  EpistemicFoundationResult,
  ClaimTraceResult,
  EvidenceToClaimTraceResult,
  InferenceValidationResult,
  FormalRule,
} from "./types";
import { FORMAL_RULES } from "./contract-constants";

type EvidenceStore = Map<EvidenceId, EvidenceItem>;
type ClaimStore = Map<ClaimId, Claim>;
type InferenceStore = Map<InferenceId, Inference>;
type DerivationStore = Map<string, DerivationStep>;
type ContradictionStore = Map<string, ContradictionRecord>;
type RevisionStore = Map<string, KnowledgeRevisionEntry[]>;
type UnknownStore = Map<string, UnknownState>;
type AssumptionStore = Map<string, AssumptionRecord>;
type ViolationStore = Map<string, EpistemicViolationRecord>;

export interface EpistemicFoundationEngine {
  addEvidence(evidence: Omit<EvidenceItem, "id" | "createdAt">): EvidenceItem;
  addClaim(claim: Omit<Claim, "id" | "createdAt" | "updatedAt">): Claim;
  addInference(inference: Omit<Inference, "id" | "createdAt" | "updatedAt">): Inference;
  addDerivationStep(step: Omit<DerivationStep, "id" | "timestamp">): DerivationStep;
  markUnknown(params: {
    subject: string;
    predicate: string;
    absenceModel: AbsenceModel;
    confidence: number;
    notes?: string;
  }): UnknownState;
  recordAssumption(params: {
    statement: string;
    inferenceId?: InferenceId;
    claimId?: ClaimId;
    risk: "low" | "medium" | "high";
    explicit: boolean;
  }): AssumptionRecord;
  recordViolation(violation: EpistemicViolationRecord): EpistemicViolationRecord;
  reviseClaim(params: {
    claimId: ClaimId;
    newStatus: EpistemicStatus;
    newStatement?: string;
    triggerEvidence: EvidenceId[];
    reason: string;
  }): KnowledgeRevisionEntry;
  supersedeClaim(params: {
    claimId: ClaimId;
    supersededBy: ClaimId;
    reason: string;
  }): void;
  addContradiction(params: {
    claimA: ClaimId;
    claimB: ClaimId;
    evidenceA: EvidenceId[];
    evidenceB: EvidenceId[];
    resolution: ContradictionRecord["resolution"];
    preferredClaimId?: ClaimId;
    resolutionReason?: string;
  }): ContradictionRecord;
  validateInference(inferenceId: InferenceId): InferenceValidationResult;
  traceClaim(claimId: ClaimId): ClaimTraceResult;
  traceEvidenceToClaims(evidenceId: EvidenceId): EvidenceToClaimTraceResult;
  query(params: EpistemicFoundationQuery): EpistemicFoundationResult;
  getEvidence(id: EvidenceId): EvidenceItem | undefined;
  getClaim(id: ClaimId): Claim | undefined;
  getInference(id: InferenceId): Inference | undefined;
  reset(): void;
}

let evidenceIdCounter = 0;
let claimIdCounter = 0;
let inferenceIdCounter = 0;
let derivationIdCounter = 0;
let revisionIdCounter = 0;
let contradictionIdCounter = 0;
let assumptionIdCounter = 0;
let violationIdCounter = 0;

function nowISO(): string {
  return new Date().toISOString();
}

function nextEvidenceId(): EvidenceId {
  return `ev_${Date.now().toString(36)}_${(evidenceIdCounter++).toString(36)}`;
}

function nextClaimId(): ClaimId {
  return `cl_${Date.now().toString(36)}_${(claimIdCounter++).toString(36)}`;
}

function nextInferenceId(): InferenceId {
  return `inf_${Date.now().toString(36)}_${(inferenceIdCounter++).toString(36)}`;
}

function nextDerivationId(): DerivationId {
  return `drv_${Date.now().toString(36)}_${(derivationIdCounter++).toString(36)}`;
}

function nextRevisionId(): string {
  return `rev_${Date.now().toString(36)}_${(revisionIdCounter++).toString(36)}`;
}

function nextContradictionId(): string {
  return `ctr_${Date.now().toString(36)}_${(contradictionIdCounter++).toString(36)}`;
}

function nextAssumptionId(): string {
  return `asm_${Date.now().toString(36)}_${(assumptionIdCounter++).toString(36)}`;
}

function nextViolationId(): string {
  return `vio_${Date.now().toString(36)}_${(violationIdCounter++).toString(36)}`;
}

export function createEpistemicFoundationEngine(): EpistemicFoundationEngine {
  const evidenceStore: EvidenceStore = new Map();
  const claimStore: ClaimStore = new Map();
  const inferenceStore: InferenceStore = new Map();
  const derivationStore: DerivationStore = new Map();
  const contradictionStore: ContradictionStore = new Map();
  const revisionStore: RevisionStore = new Map();
  const unknownStore: UnknownStore = new Map();
  const assumptionStore: AssumptionStore = new Map();
  const violationStore: ViolationStore = new Map();

  function findViolatingRule(violationType: EpistemicViolation): FormalRule | undefined {
    return FORMAL_RULES.find((r) => r.violation === violationType);
  }

  function recordViolation(params: {
    violation: EpistemicViolation;
    description: string;
    objectId?: string;
    objectType?: "evidence" | "claim" | "inference";
    corrected?: boolean;
    correction?: string;
  }): EpistemicViolationRecord {
    const rule = findViolatingRule(params.violation);
    const record: EpistemicViolationRecord = {
      id: nextViolationId(),
      violation: params.violation,
      rule: rule?.id ?? ("UNKNOWN_RULE" as EpistemicRule),
      description: params.description,
      objectId: params.objectId,
      objectType: params.objectType,
      timestamp: nowISO(),
      corrected: params.corrected ?? false,
      correction: params.correction,
    };
    violationStore.set(record.id, record);
    return record;
  }

  function validateClaimStatus(status: EpistemicStatus, lifecycleStatus: ClaimLifecycleStatus): boolean {
    if (status === "direct" || status === "documented") {
      return lifecycleStatus === "established" || lifecycleStatus === "supported";
    }
    if (status === "reported") {
      return lifecycleStatus === "supported" || lifecycleStatus === "established";
    }
    if (status === "derived") {
      return lifecycleStatus === "supported" || lifecycleStatus === "established";
    }
    if (status === "inferred") {
      return lifecycleStatus === "proposed" || lifecycleStatus === "uncertain";
    }
    if (status === "assumed") {
      return lifecycleStatus === "proposed";
    }
    if (status === "unknown") {
      return lifecycleStatus === "unknown";
    }
    if (status === "unsupported") {
      return lifecycleStatus === "rejected";
    }
    if (status === "contradicted") {
      return lifecycleStatus === "contradicted";
    }
    return false;
  }

  function validateEvidenceSufficiencyForClaim(
    claim: Omit<Claim, "id" | "createdAt" | "updatedAt">,
  ): { sufficient: boolean; missing: string[] } {
    const missing: string[] = [];
    if (claim.provenance.evidenceIds.length === 0 && claim.status !== "unknown" && claim.status !== "unsupported") {
      missing.push("No supporting evidence");
    }
    if (
      (claim.status === "derived" || claim.status === "inferred") &&
      claim.provenance.derivationIds.length === 0 &&
      claim.provenance.inferenceIds.length === 0
    ) {
      missing.push("Derived/inferred claim missing derivation or inference provenance");
    }
    if (claim.assumptions.length > 0 && claim.status === "direct") {
      missing.push("Direct claim cannot have assumptions");
    }
    return { sufficient: missing.length === 0, missing };
  }

  return {
    addEvidence(evidence: Omit<EvidenceItem, "id" | "createdAt">): EvidenceItem {
      const id = nextEvidenceId();
      const item: EvidenceItem = {
        ...evidence,
        id,
        createdAt: nowISO(),
        contradictions: evidence.contradictions ?? [],
        qualityFlags: evidence.qualityFlags ?? [],
      };
      evidenceStore.set(id, item);
      return item;
    },

    addClaim(claim: Omit<Claim, "id" | "createdAt" | "updatedAt">): Claim {
      const id = nextClaimId();
      const sufficiency = validateEvidenceSufficiencyForClaim(claim);
      if (!sufficiency.sufficient && claim.status !== "unknown" && claim.status !== "unsupported") {
        recordViolation({
          violation: "UNSUPPORTED_CLAIM",
          description: `Claim "${claim.statement}" added with insufficient support: ${sufficiency.missing.join(", ")}`,
          objectId: id,
          objectType: "claim",
        });
      }
      if (!validateClaimStatus(claim.status, claim.lifecycleStatus)) {
        recordViolation({
          violation: "STATUS_COLLAPSE",
          description: `Claim status "${claim.status}" incompatible with lifecycle status "${claim.lifecycleStatus}"`,
          objectId: id,
          objectType: "claim",
        });
      }
      if (claim.status === "inferred" && claim.provenance.inferenceIds.length === 0) {
        recordViolation({
          violation: "INFERENCE_AS_FACT",
          description: `Inferred claim "${claim.statement}" has no inference provenance`,
          objectId: id,
          objectType: "claim",
        });
      }
      if (claim.status === "reported" && claim.provenance.evidenceIds.length === 0) {
        recordViolation({
          violation: "REPORTED_AS_OBSERVED",
          description: `Reported claim "${claim.statement}" has no evidence provenance`,
          objectId: id,
          objectType: "claim",
        });
      }
      const item: Claim = {
        ...claim,
        id,
        createdAt: nowISO(),
        updatedAt: nowISO(),
      };
      claimStore.set(id, item);
      return item;
    },

    addInference(inference: Omit<Inference, "id" | "createdAt" | "updatedAt">): Inference {
      const id = nextInferenceId();
      const violations: string[] = [];
      if (inference.inputs.evidenceIds.length === 0 && inference.inputs.claimIds.length === 0) {
        violations.push("Inference has no inputs");
      }
      if (inference.confidence.overall < 0 || inference.confidence.overall > 1) {
        violations.push(`Invalid overall confidence: ${inference.confidence.overall}`);
      }
      if (violations.length > 0) {
        recordViolation({
          violation: "UNSUPPORTED_CLAIM",
          description: `Inference validation failed: ${violations.join(", ")}`,
          objectId: id,
          objectType: "inference",
        });
      }
      const item: Inference = {
        ...inference,
        id,
        createdAt: nowISO(),
        updatedAt: nowISO(),
      };
      inferenceStore.set(id, item);
      return item;
    },

    addDerivationStep(step: Omit<DerivationStep, "id" | "timestamp">): DerivationStep {
      const id = nextDerivationId();
      const item: DerivationStep = {
        ...step,
        id,
        timestamp: nowISO(),
      };
      derivationStore.set(id, item);
      return item;
    },

    markUnknown(params: {
      subject: string;
      predicate: string;
      absenceModel: AbsenceModel;
      confidence: number;
      notes?: string;
    }): UnknownState {
      const key = `${params.subject}::${params.predicate}`;
      const state: UnknownState = {
        subject: params.subject,
        predicate: params.predicate,
        absenceModel: params.absenceModel,
        lastChecked: nowISO(),
        confidence: params.confidence,
        notes: params.notes,
      };
      unknownStore.set(key, state);
      return state;
    },

    recordAssumption(params: {
      statement: string;
      inferenceId?: InferenceId;
      claimId?: ClaimId;
      risk: "low" | "medium" | "high";
      explicit: boolean;
    }): AssumptionRecord {
      const id = nextAssumptionId();
      const record: AssumptionRecord = {
        id,
        statement: params.statement,
        attachedToInferenceId: params.inferenceId,
        attachedToClaimId: params.claimId,
        risk: params.risk,
        explicit: params.explicit,
        createdAt: nowISO(),
      };
      assumptionStore.set(id, record);
      if (!params.explicit && params.risk === "high") {
        recordViolation({
          violation: "HIDDEN_ASSUMPTION",
          description: `High-risk implicit assumption detected: "${params.statement}"`,
          objectId: params.inferenceId ?? params.claimId,
          objectType: params.inferenceId ? "inference" : "claim",
        });
      }
      return record;
    },

    recordViolation(violation: EpistemicViolationRecord): EpistemicViolationRecord {
      violationStore.set(violation.id, violation);
      return violation;
    },

    reviseClaim(params: {
      claimId: ClaimId;
      newStatus: EpistemicStatus;
      newStatement?: string;
      triggerEvidence: EvidenceId[];
      reason: string;
    }): KnowledgeRevisionEntry {
      const claim = claimStore.get(params.claimId);
      if (!claim) {
        throw new Error(`Claim not found: ${params.claimId}`);
      }
      const entry: KnowledgeRevisionEntry = {
        id: nextRevisionId(),
        claimId: params.claimId,
        previousStatus: claim.status,
        previousStatement: claim.statement,
        newStatus: params.newStatus,
        newStatement: params.newStatement,
        triggerEvidence: params.triggerEvidence,
        reason: params.reason,
        timestamp: nowISO(),
      };
      const existing = revisionStore.get(params.claimId) ?? [];
      existing.push(entry);
      revisionStore.set(params.claimId, existing);
      claim.status = params.newStatus;
      if (params.newStatement) {
        claim.statement = params.newStatement;
      }
      claim.updatedAt = nowISO();
      claimStore.set(params.claimId, claim);
      return entry;
    },

    supersedeClaim(params: { claimId: ClaimId; supersededBy: ClaimId; reason: string }): void {
      const claim = claimStore.get(params.claimId);
      const superseder = claimStore.get(params.supersededBy);
      if (!claim || !superseder) {
        throw new Error(`Claim not found: ${params.claimId} or ${params.supersededBy}`);
      }
      claim.lifecycleStatus = "superseded";
      claim.supersededBy = params.supersededBy;
      claim.updatedAt = nowISO();
      claimStore.set(params.claimId, claim);
      superseder.supersedes = params.claimId;
      superseder.updatedAt = nowISO();
      claimStore.set(params.supersededBy, superseder);
    },

    addContradiction(params: {
      claimA: ClaimId;
      claimB: ClaimId;
      evidenceA: EvidenceId[];
      evidenceB: EvidenceId[];
      resolution: ContradictionRecord["resolution"];
      preferredClaimId?: ClaimId;
      resolutionReason?: string;
    }): ContradictionRecord {
      const id = nextContradictionId();
      const record: ContradictionRecord = {
        id,
        claimA: params.claimA,
        claimB: params.claimB,
        evidenceA: params.evidenceA,
        evidenceB: params.evidenceB,
        resolution: params.resolution,
        preferredClaimId: params.preferredClaimId,
        resolutionReason: params.resolutionReason,
        createdAt: nowISO(),
      };
      if (params.resolution !== "unresolved") {
        record.resolvedAt = nowISO();
      }
      contradictionStore.set(id, record);
      const claimA = claimStore.get(params.claimA);
      const claimB = claimStore.get(params.claimB);
      if (claimA) {
        claimA.contradictions.push(params.claimB);
        claimA.status = "contradicted";
        claimStore.set(params.claimA, claimA);
      }
      if (claimB) {
        claimB.contradictions.push(params.claimA);
        claimB.status = "contradicted";
        claimStore.set(params.claimB, claimB);
      }
      for (const eid of params.evidenceA) {
        const ev = evidenceStore.get(eid);
        if (ev && !ev.contradictions.includes(eid)) {
          ev.contradictions.push(...params.evidenceB.filter((e) => e !== eid));
          evidenceStore.set(eid, ev);
        }
      }
      for (const eid of params.evidenceB) {
        const ev = evidenceStore.get(eid);
        if (ev && !ev.contradictions.includes(eid)) {
          ev.contradictions.push(...params.evidenceA.filter((e) => e !== eid));
          evidenceStore.set(eid, ev);
        }
      }
      return record;
    },

    validateInference(inferenceId: InferenceId): InferenceValidationResult {
      const inference = inferenceStore.get(inferenceId);
      if (!inference) {
        throw new Error(`Inference not found: ${inferenceId}`);
      }
      const violations: EpistemicViolationRecord[] = [];
      const missingEvidence: EvidenceId[] = [];
      const missingClaims: ClaimId[] = [];
      if (inference.inputs.evidenceIds.length === 0 && inference.inputs.claimIds.length === 0) {
        violations.push(
          recordViolation({
            violation: "UNSUPPORTED_CLAIM",
            description: `Inference ${inferenceId} has no supporting evidence or claims`,
            objectId: inferenceId,
            objectType: "inference",
          }),
        );
      } else {
        for (const eid of inference.inputs.evidenceIds) {
          if (!evidenceStore.has(eid)) {
            missingEvidence.push(eid);
          }
        }
        for (const cid of inference.inputs.claimIds) {
          if (!claimStore.has(cid)) {
            missingClaims.push(cid);
          }
        }
      }
      const contradictoryEvidence = inference.contradictoryEvidence
        .map((eid) => evidenceStore.get(eid))
        .filter((e): e is EvidenceItem => e !== undefined);
      const contradictoryClaims = inference.contradictoryClaims
        .map((cid) => claimStore.get(cid))
        .filter((c): c is Claim => c !== undefined);
      return {
        inference,
        isValid: violations.length === 0 && missingEvidence.length === 0 && missingClaims.length === 0,
        violations,
        missingSupport: { evidence: missingEvidence, claims: missingClaims },
        contradictoryEvidence,
        contradictoryClaims,
      };
    },

    traceClaim(claimId: ClaimId): ClaimTraceResult {
      const claim = claimStore.get(claimId);
      if (!claim) {
        throw new Error(`Claim not found: ${claimId}`);
      }
      const supportingEvidence = claim.provenance.evidenceIds
        .map((eid) => evidenceStore.get(eid))
        .filter((e): e is EvidenceItem => e !== undefined);
      const supportingClaims = claim.provenance.sourceClaims
        .map((cid) => claimStore.get(cid))
        .filter((c): c is Claim => c !== undefined);
      const allDerivations = Array.from(derivationStore.values()).filter((d) => d.outputClaimId === claimId);
      const inferences = claim.provenance.inferenceIds
        .map((iid) => inferenceStore.get(iid))
        .filter((i): i is Inference => i !== undefined);
      const contradictions = Array.from(contradictionStore.values()).filter(
        (c) => c.claimA === claimId || c.claimB === claimId,
      );
      const revisionHistory = revisionStore.get(claimId) ?? [];
      return {
        claim,
        provenance: claim.provenance,
        supportingEvidence,
        supportingClaims,
        sourceClaims: supportingClaims,
        derivationChain: allDerivations,
        inferences,
        contradictions,
        revisionHistory,
      };
    },

    traceEvidenceToClaims(evidenceId: EvidenceId): EvidenceToClaimTraceResult {
      const evidence = evidenceStore.get(evidenceId);
      if (!evidence) {
        throw new Error(`Evidence not found: ${evidenceId}`);
      }
      const allClaims = Array.from(claimStore.values());
      const directClaims: Claim[] = [];
      const derivedClaims: Claim[] = [];
      const inferredClaims: Claim[] = [];
      for (const claim of allClaims) {
        if (claim.provenance.evidenceIds.includes(evidenceId)) {
          if (claim.status === "direct" || claim.status === "documented" || claim.status === "reported") {
            directClaims.push(claim);
          } else if (claim.status === "derived") {
            derivedClaims.push(claim);
          } else if (claim.status === "inferred") {
            inferredClaims.push(claim);
          }
        }
      }
      const contradictions = Array.from(contradictionStore.values()).filter(
        (c) => c.evidenceA.includes(evidenceId) || c.evidenceB.includes(evidenceId),
      );
      return {
        evidence,
        directClaims,
        derivedClaims,
        inferredClaims,
        contradictions,
      };
    },

    query(params: EpistemicFoundationQuery): EpistemicFoundationResult {
      let claims = Array.from(claimStore.values());
      let evidence = Array.from(evidenceStore.values());
      let inferences = Array.from(inferenceStore.values());
      if (params.claimId) {
        claims = claims.filter((c) => c.id === params.claimId);
      }
      if (params.evidenceId) {
        evidence = evidence.filter((e) => e.id === params.evidenceId);
      }
      if (params.inferenceId) {
        inferences = inferences.filter((i) => i.id === params.inferenceId);
      }
      if (params.subject) {
        claims = claims.filter((c) => c.subject === params.subject);
      }
      if (params.status) {
        claims = claims.filter((c) => c.status === params.status);
      }
      if (!params.includeSuperseded) {
        claims = claims.filter((c) => c.lifecycleStatus !== "superseded");
      }
      if (!params.includeContradictions) {
        claims = claims.filter((c) => c.status !== "contradicted");
      }
      if (params.asOf) {
        const asOfDate = new Date(params.asOf);
        claims = claims.filter((c) => new Date(c.createdAt) <= asOfDate);
        evidence = evidence.filter((e) => new Date(e.createdAt) <= asOfDate);
        inferences = inferences.filter((i) => new Date(i.createdAt) <= asOfDate);
      }
      const known = claims.filter((c) => c.status === "direct" || c.status === "documented" || c.status === "derived");
      const inferred = inferences;
      const unknown = Array.from(unknownStore.values());
      const unsupported = claims.filter((c) => c.status === "unsupported");
      const contradicted = claims.filter((c) => c.status === "contradicted");
      const violations = Array.from(violationStore.values());
      return {
        known,
        inferred,
        unknown,
        unsupported,
        contradicted,
        evidence,
        violations,
        totalClaims: claims.length,
        totalEvidence: evidence.length,
        totalInferences: inferences.length,
      };
    },

    getEvidence(id: EvidenceId): EvidenceItem | undefined {
      return evidenceStore.get(id);
    },

    getClaim(id: ClaimId): Claim | undefined {
      return claimStore.get(id);
    },

    getInference(id: InferenceId): Inference | undefined {
      return inferenceStore.get(id);
    },

    reset(): void {
      evidenceStore.clear();
      claimStore.clear();
      inferenceStore.clear();
      derivationStore.clear();
      contradictionStore.clear();
      revisionStore.clear();
      unknownStore.clear();
      assumptionStore.clear();
      violationStore.clear();
      evidenceIdCounter = 0;
      claimIdCounter = 0;
      inferenceIdCounter = 0;
      derivationIdCounter = 0;
      revisionIdCounter = 0;
      contradictionIdCounter = 0;
      assumptionIdCounter = 0;
      violationIdCounter = 0;
    },
  };
}
