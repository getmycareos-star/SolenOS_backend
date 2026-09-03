import {
  createEpistemicFoundationEngine,
  EPISTEMIC_STATUSES,
  CLAIM_LIFECYCLE_STATUSES,
  EVIDENCE_KINDS,
  FORMAL_RULES,
} from "../src/lib/epistemic-foundation";

let passed = 0;
let failed = 0;

function assert(condition: boolean, message: string) {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`✗ FAIL: ${message}`);
  }
}

function section(name: string) {
  console.log(`\n=== ${name} ===`);
}

section("Example 1 — Daughter reports Mom missed two doses of metformin");

const engine1 = createEpistemicFoundationEngine();

const evidence1 = engine1.addEvidence({
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
  temporalScope: { isHistorical: false, isCurrent: true },
  contradictions: [],
  qualityFlags: ["second_hand_report"],
});

const reportedClaim = engine1.addClaim({
  statement: "Mom missed two doses of metformin.",
  status: "reported",
  lifecycleStatus: "supported",
  subject: "Mom",
  confidence: 0.8,
  uncertaintyLevel: "reported",
  provenance: {
    evidenceIds: [evidence1.id],
    derivationIds: [],
    inferenceIds: [],
    sourceClaims: [],
    originalSources: [evidence1.source],
    reasoningSummary: "Extracted from daughter's report",
  },
  assumptions: [],
  contradictions: [],
});

const result1 = engine1.query({ claimId: reportedClaim.id, includeContradictions: true });
assert(result1.known.length === 0, "reported claim must NOT appear in known");
assert(result1.unsupported.length === 0, "reported claim must NOT be unsupported");
assert(result1.totalClaims === 1, "exactly one claim exists");
assert(result1.evidence.length === 1, "exactly one evidence exists");

const trace1 = engine1.traceClaim(reportedClaim.id);
assert(trace1.supportingEvidence.length === 1, "trace returns supporting evidence");
assert(trace1.supportingEvidence[0].id === evidence1.id, "trace evidence matches added evidence");
assert(trace1.claim.status === "reported", "trace preserves reported status");
assert(trace1.claim.lifecycleStatus === "supported", "trace preserves supported lifecycle");

const badInferenceAsFact = engine1.addClaim({
  statement: "Mom is non-adherent to her medication.",
  status: "inferred",
  lifecycleStatus: "established",
  subject: "Mom",
  confidence: 0.7,
  uncertaintyLevel: "suspected",
  provenance: {
    evidenceIds: [evidence1.id],
    derivationIds: [],
    inferenceIds: [],
    sourceClaims: [],
    originalSources: [evidence1.source],
  },
  assumptions: ["Non-adherence defined as missing two doses"],
  contradictions: [],
});

const violationsAfterBadClaim = Array.from(engine1.query({}).violations);
assert(
  violationsAfterBadClaim.some((v) => v.violation === "INFERENCE_AS_FACT" || v.violation === "STATUS_COLLAPSE"),
  "inference presented as fact must be recorded as violation",
);

engine1.markUnknown({
  subject: "Mom",
  predicate: "fall_history",
  absenceModel: "not_mentioned",
  confidence: 0.0,
  notes: "No record mentions whether Mom has fallen.",
});

const unknownResult1 = engine1.query({});
assert(unknownResult1.unknown.length === 1, "unknown state must be recorded");
assert(unknownResult1.unknown[0].predicate === "fall_history", "unknown predicate preserved");
assert(unknownResult1.unknown[0].absenceModel === "not_mentioned", "absence model preserved");

const falseClaim1 = engine1.addClaim({
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

const violationsAfterFalseClaim1 = Array.from(engine1.query({}).violations);
assert(
  violationsAfterFalseClaim1.some((v) => v.violation === "UNSUPPORTED_CLAIM"),
  "claim from absence of evidence must be unsupported",
);

section("Example 2 — Medication start then discontinuation");

const engine2 = createEpistemicFoundationEngine();

const evidence2a = engine2.addEvidence({
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

const claim2a = engine2.addClaim({
  statement: "Patient takes metformin.",
  status: "documented",
  lifecycleStatus: "established",
  subject: "Patient",
  confidence: 0.95,
  uncertaintyLevel: "known",
  provenance: {
    evidenceIds: [evidence2a.id],
    derivationIds: [],
    inferenceIds: [],
    sourceClaims: [],
    originalSources: [evidence2a.source],
  },
  assumptions: [],
  contradictions: [],
});

const evidence2b = engine2.addEvidence({
  source: {
    id: "src_b",
    type: "clinician_documentation",
    label: "Clinic note 2026-08-27",
    capturedAt: "2026-08-27T09:00:00Z",
  },
  content: "Metformin discontinued.",
  extractedFact: "Metformin discontinued.",
  confidence: 0.95,
  contradictions: [evidence2a.id],
  qualityFlags: ["contradicts_prior"],
});

const revision = engine2.reviseClaim({
  claimId: claim2a.id,
  newStatus: "contradicted",
  newStatement:
    "Patient takes metformin (historically, as of 2026-08-20). Current status: discontinued as of 2026-08-27.",
  triggerEvidence: [evidence2b.id],
  reason: "New documentation contradicts prior claim",
});

assert(revision.previousStatus === "documented", "previous status preserved");
assert(revision.newStatus === "contradicted", "new status applied");
assert(revision.triggerEvidence.includes(evidence2b.id), "trigger evidence recorded");

const trace2a = engine2.traceClaim(claim2a.id);
assert(trace2a.claim.status === "contradicted", "claim status updated");
assert(trace2a.revisionHistory.length === 1, "revision history recorded");
assert(trace2a.revisionHistory[0].reason === "New documentation contradicts prior claim", "revision reason preserved");

const claim2b = engine2.addClaim({
  statement: "Metformin is discontinued.",
  status: "documented",
  lifecycleStatus: "established",
  subject: "Patient",
  confidence: 0.95,
  uncertaintyLevel: "known",
  provenance: {
    evidenceIds: [evidence2b.id],
    derivationIds: [],
    inferenceIds: [],
    sourceClaims: [],
    originalSources: [evidence2b.source],
  },
  assumptions: [],
  contradictions: [],
});

const contradiction = engine2.addContradiction({
  claimA: claim2a.id,
  claimB: claim2b.id,
  evidenceA: [evidence2a.id],
  evidenceB: [evidence2b.id],
  resolution: "unresolved",
});

assert(contradiction.resolution === "unresolved", "contradiction remains unresolved");
assert(contradiction.evidenceA.includes(evidence2a.id), "evidence A recorded");
assert(contradiction.evidenceB.includes(evidence2b.id), "evidence B recorded");
assert(!contradiction.preferredClaimId, "no preferred claim when unresolved");

const trace2b = engine2.traceClaim(claim2a.id);
assert(trace2b.contradictions.length === 1, "contradiction attached to claim A");
assert(trace2b.contradictions[0].id === contradiction.id, "contradiction id matches");

assert(
  engine2.getEvidence(evidence2a.id)?.contradictions.includes(evidence2b.id),
  "R6: bidirectional contradiction preserved on evidence A",
);
assert(
  engine2.getEvidence(evidence2b.id)?.contradictions.includes(evidence2a.id),
  "R6: bidirectional contradiction preserved on evidence B",
);

section("Example 3 — Fall frequency increasing");

const engine3 = createEpistemicFoundationEngine();

const evidence3 = engine3.addEvidence({
  source: {
    id: "src_3",
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

const directClaim3 = engine3.addClaim({
  statement: "Patient fell three times in two weeks.",
  status: "reported",
  lifecycleStatus: "supported",
  subject: "Patient",
  confidence: 0.85,
  uncertaintyLevel: "reported",
  provenance: {
    evidenceIds: [evidence3.id],
    derivationIds: [],
    inferenceIds: [],
    sourceClaims: [],
    originalSources: [evidence3.source],
  },
  assumptions: [],
  contradictions: [],
});

const derivedClaim3 = engine3.addClaim({
  statement: "Fall frequency appears to be increasing.",
  status: "derived",
  lifecycleStatus: "supported",
  subject: "Patient",
  confidence: 0.6,
  uncertaintyLevel: "probable",
  provenance: {
    evidenceIds: [evidence3.id],
    derivationIds: [],
    inferenceIds: [],
    sourceClaims: [directClaim3.id],
    originalSources: [evidence3.source],
    reasoningSummary: "Three falls in two weeks exceeds typical baseline",
  },
  assumptions: ["Two-week baseline is representative", "No prior fall history available"],
  contradictions: [],
});

engine3.addDerivationStep({
  operation: "temporal_reasoning",
  inputIds: [evidence3.id, directClaim3.id],
  outputClaimId: derivedClaim3.id,
  reasoning: "Three falls in two weeks exceeds typical baseline",
  timestamp: "2026-08-27T10:01:00Z",
});

const trace3 = engine3.traceClaim(derivedClaim3.id);
assert(trace3.claim.status === "derived", "derived claim status preserved");
assert(trace3.claim.lifecycleStatus === "supported", "derived claim lifecycle preserved");
assert(trace3.derivationChain.length === 1, "derivation chain recorded");
assert(trace3.derivationChain[0].operation === "temporal_reasoning", "derivation operation preserved");

const query3 = engine3.query({ subject: "Patient" });
const derived3 = query3.known.filter((c) => c.status === "derived");
assert(derived3.length === 1, "derived claim present in known");
assert(derived3[0].statement === "Fall frequency appears to be increasing.", "derived statement preserved");

const inference3 = engine3.addInference({
  statement: "The patient may have increasing mobility or safety concerns.",
  inputs: {
    evidenceIds: [evidence3.id],
    claimIds: [directClaim3.id],
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

const validation3 = engine3.validateInference(inference3.id);
assert(validation3.isValid === true, "inference with inputs is valid");
assert(validation3.inference.confidence.overall === 0.5, "inference confidence preserved");
assert(validation3.inference.confidence.dimensions.inference_validity === 0.5, "inference validity dimension preserved");

section("Example 4 — No available fall evidence");

const engine4 = createEpistemicFoundationEngine();

engine4.markUnknown({
  subject: "Patient",
  predicate: "fall_history",
  absenceModel: "not_mentioned",
  confidence: 0.0,
  notes: "No record mentions whether the patient has fallen.",
});

const falseClaim4 = engine4.addClaim({
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

const violationsAfterFalseClaim4 = Array.from(engine4.query({}).violations);
assert(
  violationsAfterFalseClaim4.some((v) => v.violation === "UNSUPPORTED_CLAIM"),
  "false claim from absence must be unsupported",
);

const unknownResult4 = engine4.query({});
assert(unknownResult4.unknown.length === 1, "unknown state recorded");
assert(unknownResult4.unknown.some((u) => u.absenceModel === "not_mentioned"), "absence model preserved as not_mentioned");

section("Example 5 — Model-generated speculative statement");

const engine5 = createEpistemicFoundationEngine();

const evidence5 = engine5.addEvidence({
  source: {
    id: "src_5",
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

const inference5 = engine5.addInference({
  statement: "The patient appears to be becoming less independent.",
  inputs: {
    evidenceIds: [evidence5.id],
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

engine5.recordAssumption({
  statement: "Medication adherence difficulty indicates declining independence",
  inferenceId: inference5.id,
  risk: "high",
  explicit: true,
});

engine5.recordAssumption({
  statement: "Single report is representative of ongoing trend",
  inferenceId: inference5.id,
  risk: "high",
  explicit: true,
});

const validation5 = engine5.validateInference(inference5.id);
assert(validation5.isValid === true, "inference with explicit assumptions is valid");
assert(validation5.inference.confidence.overall === 0.4, "low inference confidence preserved");
assert(validation5.inference.assumptions.length === 2, "assumptions preserved on inference");

const query5 = engine5.query({});
assert(query5.inferred.length === 1, "inference present in inferred layer");
assert(
  query5.inferred.some((i) => i.statement === "The patient appears to be becoming less independent."),
  "speculative inference present in inferred layer",
);

section("Formal rules enforcement");

const engineRules = createEpistemicFoundationEngine();

const evidenceR1 = engineRules.addEvidence({
  source: {
    id: "src_r1",
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

const claimR1 = engineRules.addClaim({
  statement: "Mom's blood pressure is high.",
  status: "reported",
  lifecycleStatus: "supported",
  subject: "Mom",
  confidence: 0.7,
  uncertaintyLevel: "reported",
  provenance: {
    evidenceIds: [evidenceR1.id],
    derivationIds: [],
    inferenceIds: [],
    sourceClaims: [],
    originalSources: [evidenceR1.source],
  },
  assumptions: [],
  contradictions: [],
});

assert(claimR1.status === "reported", "R1: evidence does not auto-become direct fact");
assert(claimR1.uncertaintyLevel === "reported", "R1: uncertainty preserved");

engineRules.markUnknown({
  subject: "Patient",
  predicate: "has_fallen",
  absenceModel: "unknown",
  confidence: 0.0,
});

const resultR3 = engineRules.query({});
assert(resultR3.unknown.length >= 1, "R3: unknown state exists");
assert(resultR3.known.filter((c) => c.subject === "Patient" && c.statement === "The patient has not fallen.").length === 0, "R3: unknown not converted to false claim");

const unsupportedR5 = engineRules.addClaim({
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

const resultR5 = engineRules.query({});
assert(resultR5.unsupported.length >= 1, "R5: unsupported claims tracked");
assert(resultR5.unsupported.some((c) => c.id === unsupportedR5.id), "R5: specific unsupported claim tracked");

const evidenceR6a = engineRules.addEvidence({
  source: { id: "src_r6a", type: "clinician_documentation", label: "Note A", capturedAt: "2026-08-20T08:00:00Z" },
  content: "Patient takes metformin.",
  confidence: 0.95,
  contradictions: [],
  qualityFlags: [],
});

const evidenceR6b = engineRules.addEvidence({
  source: { id: "src_r6b", type: "clinician_documentation", label: "Note B", capturedAt: "2026-08-27T09:00:00Z" },
  content: "Metformin discontinued.",
  confidence: 0.95,
  contradictions: [evidenceR6a.id],
  qualityFlags: ["contradicts_prior"],
});

engineRules.addContradiction({
  claimA: "claim_r6a",
  claimB: "claim_r6b",
  evidenceA: [evidenceR6a.id],
  evidenceB: [evidenceR6b.id],
  resolution: "unresolved",
});

assert(evidenceR6b.contradictions.includes(evidenceR6a.id), "R6: contradiction recorded on evidence B");
assert(engineRules.getEvidence(evidenceR6a.id)?.contradictions.includes(evidenceR6b.id), "R6: bidirectional contradiction preserved on evidence A");

const claimR8 = engineRules.addClaim({
  statement: "Patient fell.",
  status: "reported",
  lifecycleStatus: "supported",
  subject: "Patient",
  confidence: 0.9,
  uncertaintyLevel: "reported",
  provenance: {
    evidenceIds: [evidenceR1.id],
    derivationIds: [],
    inferenceIds: [],
    sourceClaims: [],
    originalSources: [evidenceR1.source],
  },
  assumptions: [],
  contradictions: [],
});

const traceR8 = engineRules.traceClaim(claimR8.id);
assert(traceR8.supportingEvidence.length === 1, "R8: provenance returns evidence");
assert(traceR8.supportingEvidence[0].id === evidenceR1.id, "R8: evidence id matches");
assert(traceR8.claim.confidence === 0.9, "R8: confidence preserved alongside provenance");

section("Irreducible object model validation");

const engineModel = createEpistemicFoundationEngine();

const evidenceModel = engineModel.addEvidence({
  source: {
    id: "src_model",
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

const directClaimModel = engineModel.addClaim({
  statement: "Blood pressure measured at 160/95.",
  status: "direct",
  lifecycleStatus: "established",
  subject: "Patient",
  confidence: 0.99,
  uncertaintyLevel: "known",
  provenance: {
    evidenceIds: [evidenceModel.id],
    derivationIds: [],
    inferenceIds: [],
    sourceClaims: [],
    originalSources: [evidenceModel.source],
  },
  assumptions: [],
  contradictions: [],
});

const inferenceModel = engineModel.addInference({
  statement: "Blood pressure may be worsening.",
  inputs: {
    evidenceIds: [evidenceModel.id],
    claimIds: [directClaimModel.id],
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

const resultModel = engineModel.query({});
assert(resultModel.evidence.length >= 1, "model: evidence exists");
assert(resultModel.known.filter((c) => c.status === "direct" || c.status === "documented" || c.status === "derived").length >= 1, "model: known layer exists");
assert(resultModel.inferred.length >= 1, "model: inferred layer exists");
assert(resultModel.unknown.length === 0, "model: no unknowns");
assert(resultModel.unsupported.length === 0, "model: no unsupported");

const forward = engineModel.traceEvidenceToClaims(evidenceModel.id);
assert(forward.directClaims.some((c) => c.id === directClaimModel.id), "forward trace: direct claim found");

const backward = engineModel.traceClaim(directClaimModel.id);
assert(backward.supportingEvidence.some((e) => e.id === evidenceModel.id), "backward trace: evidence found");
assert(backward.claim.statement === "Blood pressure measured at 160/95.", "backward trace: statement preserved");

section("Contract constants completeness");
assert(EVIDENCE_KINDS.length > 0, "EVIDENCE_KINDS non-empty");
assert(EPISTEMIC_STATUSES.length > 0, "EPISTEMIC_STATUSES non-empty");
assert(CLAIM_LIFECYCLE_STATUSES.length > 0, "CLAIM_LIFECYCLE_STATUSES non-empty");
assert(FORMAL_RULES.length > 0, "FORMAL_RULES non-empty");

console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);
if (failed > 0) {
  process.exitCode = 1;
}
