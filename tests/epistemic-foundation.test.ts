import { describe, it, expect, beforeEach } from "vitest";
import {
  createEpistemicFoundationEngine,
  EPISTEMIC_STATUSES,
  CLAIM_LIFECYCLE_STATUSES,
  EVIDENCE_KINDS,
} from "../../../src/lib/epistemic-foundation";

describe("Epistemic Foundation — Evidence vs Knowledge vs Inference", () => {
  let engine: ReturnType<typeof createEpistemicFoundationEngine>;

  beforeEach(() => {
    engine = createEpistemicFoundationEngine();
  });

  describe("Example 1 — Daughter reports Mom missed two doses of metformin", () => {
    it("should classify reported information separately from observed/direct knowledge", () => {
      const evidence = engine.addEvidence({
        source: {
          id: "src_1",
          type: "caregiver_report",
          label: "Daughter report",
          capturedAt: "2026-08-27T10:00:00Z",
          contributorId: "caregiver_123",
        },
        content: "Daughter reports that Mom has missed two doses of metformin.",
        extractedFact: "Mom missed two doses of metformin.",
        confidence: 0.9,
        reliability: 0.8,
        temporalScope: {
          isHistorical: false,
          isCurrent: true,
        },
        contradictions: [],
        qualityFlags: ["second_hand_report"],
      });

      const reportedClaim = engine.addClaim({
        statement: "Mom missed two doses of metformin.",
        status: "reported",
        lifecycleStatus: "supported",
        subject: "Mom",
        confidence: 0.8,
        uncertaintyLevel: "reported",
        provenance: {
          evidenceIds: [evidence.id],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [evidence.source],
          reasoningSummary: "Extracted from daughter's report",
        },
        assumptions: [],
        contradictions: [],
      });

      const result = engine.query({ claimId: reportedClaim.id, includeContradictions: true });

      expect(result.known).toHaveLength(0);
      expect(result.unsupported).toHaveLength(0);
      expect(result.contradicted).toHaveLength(0);
      expect(result.totalClaims).toBe(1);
      expect(result.evidence).toHaveLength(1);

      const trace = engine.traceClaim(reportedClaim.id);
      expect(trace.supportingEvidence).toHaveLength(1);
      expect(trace.supportingEvidence[0].id).toBe(evidence.id);
      expect(trace.claim.status).toBe("reported");
      expect(trace.claim.lifecycleStatus).toBe("supported");
    });

    it("should not allow unsafe inference to become fact without explicit inference provenance", () => {
      const evidence = engine.addEvidence({
        source: {
          id: "src_1",
          type: "caregiver_report",
          label: "Daughter report",
          capturedAt: "2026-08-27T10:00:00Z",
        },
        content: "Daughter reports that Mom has missed two doses of metformin.",
        extractedFact: "Mom missed two doses of metformin.",
        confidence: 0.9,
        contradictions: [],
        qualityFlags: [],
      });

      const badInferenceAsFact = engine.addClaim({
        statement: "Mom is non-adherent to her medication.",
        status: "inferred",
        lifecycleStatus: "established",
        subject: "Mom",
        confidence: 0.7,
        uncertaintyLevel: "suspected",
        provenance: {
          evidenceIds: [evidence.id],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [evidence.source],
        },
        assumptions: ["Non-adherence defined as missing two doses"],
        contradictions: [],
      });

      const violations = Array.from(engine.query({}).violations);
      const inferenceAsFactViolations = violations.filter(
        (v) => v.violation === "INFERENCE_AS_FACT" || v.violation === "STATUS_COLLAPSE",
      );
      expect(inferenceAsFactViolations.length).toBeGreaterThan(0);
    });

    it("should mark unknown when no evidence exists for a proposition", () => {
      engine.markUnknown({
        subject: "Mom",
        predicate: "fall_history",
        absenceModel: "not_mentioned",
        confidence: 0.0,
        notes: "No record mentions whether Mom has fallen.",
      });

      const result = engine.query({});
      expect(result.unknown).toHaveLength(1);
      expect(result.unknown[0].predicate).toBe("fall_history");
      expect(result.unknown[0].absenceModel).toBe("not_mentioned");

      const badClaim = engine.addClaim({
        statement: "Mom has not fallen.",
        status: "direct",
        lifecycleStatus: "established",
        subject: "Mom",
        confidence: 1.0,
        uncertaintyLevel: "known",
        provenance: {
          evidenceIds: [],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [],
        },
        assumptions: [],
        contradictions: [],
      });

      const violations = Array.from(engine.query({}).violations);
      expect(violations.some((v) => v.violation === "UNSUPPORTED_CLAIM")).toBe(true);
    });
  });

  describe("Example 2 — Medication start then discontinuation", () => {
    it("should preserve both claims and their epistemic status through revision", () => {
      const evidence1 = engine.addEvidence({
        source: {
          id: "src_a",
          type: "clinician_documentation",
          label: "Clinic note 2026-08-20",
          capturedAt: "2026-08-20T08:00:00Z",
        },
        content: "Patient takes metformin.",
        extractedFact: "Patient takes metformin.",
        confidence: 0.95,
        contradictions: [],
        qualityFlags: [],
      });

      const claim1 = engine.addClaim({
        statement: "Patient takes metformin.",
        status: "documented",
        lifecycleStatus: "established",
        subject: "Patient",
        confidence: 0.95,
        uncertaintyLevel: "known",
        provenance: {
          evidenceIds: [evidence1.id],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [evidence1.source],
        },
        assumptions: [],
        contradictions: [],
      });

      const evidence2 = engine.addEvidence({
        source: {
          id: "src_b",
          type: "clinician_documentation",
          label: "Clinic note 2026-08-27",
          capturedAt: "2026-08-27T09:00:00Z",
        },
        content: "Metformin discontinued.",
        extractedFact: "Metformin discontinued.",
        confidence: 0.95,
        contradictions: [evidence1.id],
        qualityFlags: ["contradicts_prior"],
      });

      const revision = engine.reviseClaim({
        claimId: claim1.id,
        newStatus: "contradicted",
        newStatement: "Patient takes metformin (historically, as of 2026-08-20). Current status: discontinued as of 2026-08-27.",
        triggerEvidence: [evidence2.id],
        reason: "New documentation contradicts prior claim",
      });

      expect(revision.previousStatus).toBe("documented");
      expect(revision.newStatus).toBe("contradicted");
      expect(revision.triggerEvidence).toContain(evidence2.id);

      const trace = engine.traceClaim(claim1.id);
      expect(trace.claim.status).toBe("contradicted");
      expect(trace.revisionHistory).toHaveLength(1);
      expect(trace.revisionHistory[0].reason).toBe("New documentation contradicts prior claim");
    });

    it("should create a contradiction record when evidence conflicts", () => {
      const evidence1 = engine.addEvidence({
        source: {
          id: "src_a",
          type: "clinician_documentation",
          label: "Note A",
          capturedAt: "2026-08-20T08:00:00Z",
        },
        content: "Patient takes metformin.",
        confidence: 0.95,
        contradictions: [],
        qualityFlags: [],
      });

      const evidence2 = engine.addEvidence({
        source: {
          id: "src_b",
          type: "clinician_documentation",
          label: "Note B",
          capturedAt: "2026-08-27T09:00:00Z",
        },
        content: "Metformin discontinued.",
        confidence: 0.95,
        contradictions: [],
        qualityFlags: [],
      });

      const claim1 = engine.addClaim({
        statement: "Patient takes metformin.",
        status: "documented",
        lifecycleStatus: "established",
        subject: "Patient",
        confidence: 0.95,
        uncertaintyLevel: "known",
        provenance: {
          evidenceIds: [evidence1.id],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [evidence1.source],
        },
        assumptions: [],
        contradictions: [],
      });

      const claim2 = engine.addClaim({
        statement: "Metformin is discontinued.",
        status: "documented",
        lifecycleStatus: "established",
        subject: "Patient",
        confidence: 0.95,
        uncertaintyLevel: "known",
        provenance: {
          evidenceIds: [evidence2.id],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [evidence2.source],
        },
        assumptions: [],
        contradictions: [],
      });

      const contradiction = engine.addContradiction({
        claimA: claim1.id,
        claimB: claim2.id,
        evidenceA: [evidence1.id],
        evidenceB: [evidence2.id],
        resolution: "unresolved",
      });

      expect(contradiction.resolution).toBe("unresolved");
      expect(contradiction.evidenceA).toContain(evidence1.id);
      expect(contradiction.evidenceB).toContain(evidence2.id);
      expect(contradiction.preferredClaimId).toBeUndefined();

      const traceA = engine.traceClaim(claim1.id);
      expect(traceA.contradictions).toHaveLength(1);
      expect(traceA.contradictions[0].id).toBe(contradiction.id);
    });
  });

  describe("Example 3 — Fall frequency increasing", () => {
    it("should classify frequency claims correctly as derived, not direct", () => {
      const evidence = engine.addEvidence({
        source: {
          id: "src_1",
          type: "caregiver_report",
          label: "Caregiver report",
          capturedAt: "2026-08-27T10:00:00Z",
        },
        content: "Patient has fallen three times in two weeks.",
        extractedFact: "Patient fell three times in two weeks.",
        confidence: 0.85,
        contradictions: [],
        qualityFlags: [],
      });

      const directClaim = engine.addClaim({
        statement: "Patient fell three times in two weeks.",
        status: "reported",
        lifecycleStatus: "supported",
        subject: "Patient",
        confidence: 0.85,
        uncertaintyLevel: "reported",
        provenance: {
          evidenceIds: [evidence.id],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [evidence.source],
        },
        assumptions: [],
        contradictions: [],
      });

      const derivedClaim = engine.addClaim({
        statement: "Fall frequency appears to be increasing.",
        status: "derived",
        lifecycleStatus: "supported",
        subject: "Patient",
        confidence: 0.6,
        uncertaintyLevel: "probable",
        provenance: {
          evidenceIds: [evidence.id],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [directClaim.id],
          originalSources: [evidence.source],
          reasoningSummary: "Three falls in two weeks exceeds typical baseline",
        },
        assumptions: ["Two-week baseline is representative", "No prior fall history available"],
        contradictions: [],
      });

      engine.addDerivationStep({
        operation: "temporal_reasoning",
        inputIds: [evidence.id, directClaim.id],
        outputClaimId: derivedClaim.id,
        reasoning: "Three falls in two weeks exceeds typical baseline",
        timestamp: "2026-08-27T10:01:00Z",
      });

      const trace = engine.traceClaim(derivedClaim.id);
      expect(trace.claim.status).toBe("derived");
      expect(trace.claim.lifecycleStatus).toBe("supported");
      expect(trace.derivationChain).toHaveLength(1);
      expect(trace.derivationChain[0].operation).toBe("temporal_reasoning");

      const query = engine.query({ subject: "Patient" });
      const derived = query.known.filter((c) => c.status === "derived");
      expect(derived).toHaveLength(1);
      expect(derived[0].statement).toBe("Fall frequency appears to be increasing.");
    });

    it("should require explicit inference provenance for speculative statements", () => {
      const evidence = engine.addEvidence({
        source: {
          id: "src_1",
          type: "caregiver_report",
          label: "Caregiver report",
          capturedAt: "2026-08-27T10:00:00Z",
        },
        content: "Patient has fallen three times in two weeks.",
        confidence: 0.85,
        contradictions: [],
        qualityFlags: [],
      });

      const inference = engine.addInference({
        statement: "The patient may have increasing mobility or safety concerns.",
        inputs: {
          evidenceIds: [evidence.id],
          claimIds: [],
          assumptions: ["Fall frequency correlates with mobility/safety decline"],
        },
        reasoningType: "causal_inference",
        reasoningSteps: [
          "Three falls observed in two weeks",
          "Fall frequency exceeds expected baseline",
          "Possible increasing mobility or safety concerns",
        ],
        confidence: {
          extractionCorrectness: 0.85,
          evidenceReliability: 0.7,
          inferenceValidity: 0.5,
          overall: 0.5,
          dimensions: {
            extraction_correctness: 0.85,
            evidence_reliability: 0.7,
            inference_validity: 0.5,
          },
        },
        assumptions: ["Fall frequency correlates with mobility/safety decline"],
        contradictoryEvidence: [],
        contradictoryClaims: [],
        status: "proposed",
      });

      const validation = engine.validateInference(inference.id);
      expect(validation.isValid).toBe(true);
      expect(validation.inference.confidence.overall).toBe(0.5);
      expect(validation.inference.confidence.dimensions.inference_validity).toBe(0.5);
    });
  });

  describe("Example 4 — No available fall evidence", () => {
    it("should represent unknown state and reject false claims from absence", () => {
      engine.markUnknown({
        subject: "Patient",
        predicate: "fall_history",
        absenceModel: "not_mentioned",
        confidence: 0.0,
        notes: "No record mentions whether the patient has fallen.",
      });

      const falseClaim = engine.addClaim({
        statement: "The patient has not fallen.",
        status: "direct",
        lifecycleStatus: "established",
        subject: "Patient",
        confidence: 1.0,
        uncertaintyLevel: "known",
        provenance: {
          evidenceIds: [],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [],
        },
        assumptions: [],
        contradictions: [],
      });

      const violations = Array.from(engine.query({}).violations);
      expect(violations.some((v) => v.violation === "UNSUPPORTED_CLAIM")).toBe(true);

      const unknownResult = engine.query({});
      expect(unknownResult.unknown).toHaveLength(1);
      expect(unknownResult.unknown[0].absenceModel).toBe("not_mentioned");
    });
  });

  describe("Example 5 — Model-generated speculative statement", () => {
    it("should require inference provenance and assumptions before presentation", () => {
      const evidence = engine.addEvidence({
        source: {
          id: "src_1",
          type: "caregiver_report",
          label: "Caregiver report",
          capturedAt: "2026-08-27T10:00:00Z",
        },
        content: "Mom is having trouble remembering to take her medication.",
        extractedFact: "Mom has medication adherence difficulty.",
        confidence: 0.8,
        contradictions: [],
        qualityFlags: ["subjective_report"],
      });

      const inference = engine.addInference({
        statement: "The patient appears to be becoming less independent.",
        inputs: {
          evidenceIds: [evidence.id],
          claimIds: [],
          assumptions: [
            "Medication adherence difficulty indicates declining independence",
            "Single report is representative of ongoing trend",
          ],
        },
        reasoningType: "causal_inference",
        reasoningSteps: [
          "Medication adherence difficulty reported",
          "Adherence difficulty may indicate cognitive or functional decline",
          "Functional decline may indicate decreasing independence",
        ],
        confidence: {
          extractionCorrectness: 0.8,
          evidenceReliability: 0.6,
          inferenceValidity: 0.4,
          overall: 0.4,
          dimensions: {
            extraction_correctness: 0.8,
            evidence_reliability: 0.6,
            inference_validity: 0.4,
          },
        },
        assumptions: [
          "Medication adherence difficulty indicates declining independence",
          "Single report is representative of ongoing trend",
        ],
        contradictoryEvidence: [],
        contradictoryClaims: [],
        status: "proposed",
      });

      engine.recordAssumption({
        statement: "Medication adherence difficulty indicates declining independence",
        inferenceId: inference.id,
        risk: "high",
        explicit: true,
      });

      engine.recordAssumption({
        statement: "Single report is representative of ongoing trend",
        inferenceId: inference.id,
        risk: "high",
        explicit: true,
      });

      const validation = engine.validateInference(inference.id);
      expect(validation.isValid).toBe(true);
      expect(validation.inference.confidence.overall).toBe(0.4);
      expect(validation.inference.assumptions).toHaveLength(2);

      const query = engine.query({});
      expect(query.inferred).toHaveLength(1);
      expect(query.inferred[0].statement).toBe("The patient appears to be becoming less independent.");
    });
  });

  describe("Formal rules enforcement", () => {
    it("should enforce R1: Evidence is not automatically truth", () => {
      const evidence = engine.addEvidence({
        source: {
          id: "src_1",
          type: "caregiver_report",
          label: "Caregiver report",
          capturedAt: "2026-08-27T10:00:00Z",
        },
        content: "Mom says her blood pressure has been high.",
        extractedFact: "Mom reports high blood pressure.",
        confidence: 0.7,
        contradictions: [],
        qualityFlags: ["second_hand", "unverified"],
      });

      const claim = engine.addClaim({
        statement: "Mom's blood pressure is high.",
        status: "reported",
        lifecycleStatus: "supported",
        subject: "Mom",
        confidence: 0.7,
        uncertaintyLevel: "reported",
        provenance: {
          evidenceIds: [evidence.id],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [evidence.source],
        },
        assumptions: [],
        contradictions: [],
      });

      expect(claim.status).toBe("reported");
      expect(claim.uncertaintyLevel).toBe("reported");
    });

    it("should enforce R3: Unknown is not false", () => {
      engine.markUnknown({
        subject: "Patient",
        predicate: "has_fallen",
        absenceModel: "unknown",
        confidence: 0.0,
      });

      const result = engine.query({});
      expect(result.unknown).toHaveLength(1);
      expect(result.known).toHaveLength(0);
    });

    it("should enforce R5: Every material claim must have traceable support", () => {
      const unsupported = engine.addClaim({
        statement: "Patient has diabetes.",
        status: "unsupported",
        lifecycleStatus: "rejected",
        subject: "Patient",
        confidence: 0.0,
        uncertaintyLevel: "unsupported",
        provenance: {
          evidenceIds: [],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [],
        },
        assumptions: [],
        contradictions: [],
      });

      const result = engine.query({});
      expect(result.unsupported).toHaveLength(1);
      expect(result.unsupported[0].id).toBe(unsupported.id);
    });

    it("should enforce R6: Contradictory evidence must remain representable", () => {
      const evidence1 = engine.addEvidence({
        source: {
          id: "src_a",
          type: "clinician_documentation",
          label: "Note A",
          capturedAt: "2026-08-20T08:00:00Z",
        },
        content: "Patient takes metformin.",
        confidence: 0.95,
        contradictions: [],
        qualityFlags: [],
      });

      const evidence2 = engine.addEvidence({
        source: {
          id: "src_b",
          type: "clinician_documentation",
          label: "Note B",
          capturedAt: "2026-08-27T09:00:00Z",
        },
        content: "Metformin discontinued.",
        confidence: 0.95,
        contradictions: [evidence1.id],
        qualityFlags: ["contradicts_prior"],
      });

      expect(evidence2.contradictions).toContain(evidence1.id);
      expect(engine.getEvidence(evidence1.id)?.contradictions).toContain(evidence2.id);
    });

    it("should enforce R8: Confidence must not replace provenance", () => {
      const evidence = engine.addEvidence({
        source: {
          id: "src_1",
          type: "caregiver_report",
          label: "Caregiver report",
          capturedAt: "2026-08-27T10:00:00Z",
        },
        content: "Patient fell.",
        confidence: 0.9,
        contradictions: [],
        qualityFlags: [],
      });

      const claim = engine.addClaim({
        statement: "Patient fell.",
        status: "reported",
        lifecycleStatus: "supported",
        subject: "Patient",
        confidence: 0.9,
        uncertaintyLevel: "reported",
        provenance: {
          evidenceIds: [evidence.id],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [evidence.source],
        },
        assumptions: [],
        contradictions: [],
      });

      const trace = engine.traceClaim(claim.id);
      expect(trace.supportingEvidence).toHaveLength(1);
      expect(trace.supportingEvidence[0].id).toBe(evidence.id);
      expect(trace.claim.confidence).toBe(0.9);
    });
  });

  describe("Irreducible object model validation", () => {
    it("should maintain exactly three fundamental epistemic layers", () => {
      const evidence = engine.addEvidence({
        source: {
          id: "src_1",
          type: "observed_measurement",
          label: "BP measurement",
          capturedAt: "2026-08-27T10:00:00Z",
        },
        content: "Blood pressure measured at 160/95.",
        extractedFact: "BP 160/95",
        confidence: 0.99,
        contradictions: [],
        qualityFlags: [],
      });

      const directClaim = engine.addClaim({
        statement: "Blood pressure measured at 160/95.",
        status: "direct",
        lifecycleStatus: "established",
        subject: "Patient",
        confidence: 0.99,
        uncertaintyLevel: "known",
        provenance: {
          evidenceIds: [evidence.id],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [evidence.source],
        },
        assumptions: [],
        contradictions: [],
      });

      const inference = engine.addInference({
        statement: "Blood pressure may be worsening.",
        inputs: {
          evidenceIds: [evidence.id],
          claimIds: [directClaim.id],
          assumptions: [],
        },
        reasoningType: "trend_analysis",
        reasoningSteps: ["BP 160/95 is elevated", "Trend suggests worsening"],
        confidence: {
          extractionCorrectness: 0.99,
          evidenceReliability: 0.9,
          inferenceValidity: 0.6,
          overall: 0.6,
          dimensions: {
            extraction_correctness: 0.99,
            evidence_reliability: 0.9,
            inference_validity: 0.6,
          },
        },
        assumptions: [],
        contradictoryEvidence: [],
        contradictoryClaims: [],
        status: "proposed",
      });

      const result = engine.query({});
      expect(result.evidence).toHaveLength(1);
      expect(result.known).toHaveLength(1);
      expect(result.inferred).toHaveLength(1);
      expect(result.unknown).toHaveLength(0);
      expect(result.unsupported).toHaveLength(0);
      expect(result.contradicted).toHaveLength(0);
    });

    it("should support forward and backward traceability", () => {
      const evidence = engine.addEvidence({
        source: {
          id: "src_1",
          type: "clinician_documentation",
          label: "Discharge summary",
          capturedAt: "2026-08-20T08:00:00Z",
        },
        content: "Patient diagnosed with Type 2 diabetes.",
        extractedFact: "Patient has Type 2 diabetes.",
        confidence: 0.95,
        contradictions: [],
        qualityFlags: [],
      });

      const claim = engine.addClaim({
        statement: "Patient has Type 2 diabetes.",
        status: "documented",
        lifecycleStatus: "established",
        subject: "Patient",
        confidence: 0.95,
        uncertaintyLevel: "known",
        provenance: {
          evidenceIds: [evidence.id],
          derivationIds: [],
          inferenceIds: [],
          sourceClaims: [],
          originalSources: [evidence.source],
        },
        assumptions: [],
        contradictions: [],
      });

      const forward = engine.traceEvidenceToClaims(evidence.id);
      expect(forward.directClaims).toHaveLength(1);
      expect(forward.directClaims[0].id).toBe(claim.id);

      const backward = engine.traceClaim(claim.id);
      expect(backward.supportingEvidence).toHaveLength(1);
      expect(backward.supportingEvidence[0].id).toBe(evidence.id);
      expect(backward.claim.statement).toBe("Patient has Type 2 diabetes.");
    });
  });

  describe("Contract constants completeness", () => {
    it("should have non-empty enum-like arrays for all key dimensions", () => {
      expect(EVIDENCE_KINDS.length).toBeGreaterThan(0);
      expect(EPISTEMIC_STATUSES.length).toBeGreaterThan(0);
      expect(CLAIM_LIFECYCLE_STATUSES.length).toBeGreaterThan(0);
      expect(EPISTEMIC_RULES.length).toBeGreaterThan(0);
      expect(EPISTEMIC_VIOLATIONS.length).toBeGreaterThan(0);
      expect(FORMAL_RULES.length).toBeGreaterThan(0);
    });
  });
});
