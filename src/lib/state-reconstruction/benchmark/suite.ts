/**
 * State Reconstruction — Benchmark Test Suite
 * SoT: docs/02-product/solenos-state-reconstruction.md
 * Tests all 15 benchmark requirements
 */

import type {
  ReconstructionInput,
  ReconstructionResult,
  CareState,
  DomainState,
  Claim,
  Contradiction,
  OpenLoop,
  SupersessionChain,
} from "./types";

import {
  BENCHMARK_REQUIREMENTS,
  FAILURE_TAXONOMY,
  CARE_STATE_DOMAINS,
} from "./contract-constants";

import {
  stateReconstructionEngine,
  reconstructCareState,
} from "./engine";

import { CanonicalCareEvent } from "../situation-entry/types";

/**
 * Benchmark test case
 */
export type BenchmarkCase = {
  id: string;
  requirement: (typeof BENCHMARK_REQUIREMENTS)[number];
  description: string;
  input: ReconstructionInput;
  validate: (result: ReconstructionResult) => { pass: boolean; details: string };
};

/**
 * Test result
 */
export type BenchmarkResult = {
  caseId: string;
  requirement: string;
  passed: boolean;
  details: string;
  durationMs: number;
};

/**
 * Run all benchmark tests
 */
export async function runBenchmarkSuite(): Promise<BenchmarkResult[]> {
  const cases = createBenchmarkCases();
  const results: BenchmarkResult[] = [];

  for (const testCase of cases) {
    const startTime = Date.now();
    try {
      const result = await reconstructCareState(testCase.input);
      const validation = testCase.validate(result);
      results.push({
        caseId: testCase.id,
        requirement: testCase.requirement,
        passed: validation.pass,
        details: validation.details,
        durationMs: Date.now() - startTime,
      });
    } catch (error) {
      results.push({
        caseId: testCase.id,
        requirement: testCase.requirement,
        passed: false,
        details: `Error: ${error}`,
        durationMs: Date.now() - startTime,
      });
    }
  }

  return results;
}

/**
 * Create all benchmark test cases
 */
function createBenchmarkCases(): BenchmarkCase[] {
  return [
    // 1. Reconstruct current state from distributed evidence
    createDistributedEvidenceTest(),

    // 2. Distinguish current from historical state
    createCurrentVsHistoricalTest(),

    // 3. Incorporate supersession
    createSupersessionTest(),

    // 4. Preserve unresolved contradictions
    createContradictionPreservationTest(),

    // 5. Propagate uncertainty
    createUncertaintyPropagationTest(),

    // 6. Preserve context-specific states
    createContextPreservationTest(),

    // 7. Reconstruct multiple domains independently
    createMultiDomainTest(),

    // 8. Distinguish capability from diagnosis
    createCapabilityVsDiagnosisTest(),

    // 9. Reconstruct medication state separately from prescription state
    createMedicationStateTest(),

    // 10. Reconstruct caregiver responsibility
    createCaregiverResponsibilityTest(),

    // 11. Avoid snapshot-only reasoning
    createNoSnapshotReasoningTest(),

    // 12. Preserve improvements
    createImprovementPreservationTest(),

    // 13. Identify stable domains
    createStableDomainTest(),

    // 14. Avoid inventing state where evidence is insufficient
    createNoInventionTest(),

    // 15. Produce traceable state to underlying claims
    createTraceabilityTest(),
  ];
}

/**
 * Test 1: Reconstruct current state from distributed evidence
 */
function createDistributedEvidenceTest(): BenchmarkCase {
  return {
    id: "bench_01_distributed_evidence",
    requirement: "reconstruct_current_state_from_distributed_evidence",
    description: "State reconstructed from multiple evidence sources across time, not single note",
    input: createDistributedEvidenceInput(),
    validate: (result) => {
      const { care_state } = result;
      const domainsWithEvidence = care_state.domains.filter((d) => d.evidence_summary.total_claims > 0);
      const hasMultipleSources = care_state.domains.some((d) =>
        d.evidence_summary.source_diversity.length > 1
      );
      const hasTrajectory = care_state.domains.some((d) => d.trajectory.length > 1);

      return {
        pass: domainsWithEvidence.length >= 3 && hasMultipleSources && hasTrajectory,
        details: `Domains with evidence: ${domainsWithEvidence.length}, Multi-source: ${hasMultipleSources}, Trajectory: ${hasTrajectory}`,
      };
    },
  };
}

/**
 * Test 2: Distinguish current from historical state
 */
function createCurrentVsHistoricalTest(): BenchmarkCase {
  return {
    id: "bench_02_current_vs_historical",
    requirement: "distinguish_current_from_historical_state",
    description: "Current state correctly identified vs historical baseline",
    input: createCurrentVsHistoricalInput(),
    validate: (result) => {
      const { care_state } = result;
      const mobility = care_state.domains.find((d) => d.domain === "physical" && d.subdomain === "mobility");
      if (!mobility) return { pass: false, details: "Mobility domain not found" };

      // Should have current value showing walker use, not independent walking (historical)
      const currentUsesWalker = mobility.current_value?.toLowerCase().includes("walker") ?? false;
      const historicalIndependent = mobility.trajectory.some(
        (t) => t.value.toLowerCase().includes("independent") && t.temporal_status === "historical"
      );

      return {
        pass: currentUsesWalker && historicalIndependent,
        details: `Current: ${mobility.current_value}, Historical independent: ${historicalIndependent}`,
      };
    },
  };
}

/**
 * Test 3: Incorporate supersession
 */
function createSupersessionTest(): BenchmarkCase {
  return {
    id: "bench_03_supersession",
    requirement: "incorporate_supersession",
    description: "Superseded claims properly handled, trajectory preserved",
    input: createSupersessionInput(),
    validate: (result) => {
      const { care_state, evidence_graph } = result;
      const standing = care_state.domains.find((d) => d.domain === "functional" && d.subdomain === "transfers");
      if (!standing) return { pass: false, details: "Transfers domain not found" };

      // Should show trajectory: 2-person -> 1-person -> independent+supervision
      const trajectory = standing.trajectory;
      const hasTwoPerson = trajectory.some((t) => t.value.includes("two-person") || t.value.includes("2-person"));
      const hasOnePerson = trajectory.some((t) => t.value.includes("one-person") || t.value.includes("1-person"));
      const hasIndependentSupervision = trajectory.some((t) => t.value.includes("independent") && t.value.includes("supervision"));

      // Should have supersession chains
      const hasSupersessionChains = evidence_graph.supersession_chains.some((c) => c.chain.length > 0);

      return {
        pass: hasTwoPerson && hasOnePerson && hasIndependentSupervision && hasSupersessionChains,
        details: `Trajectory: 2-person=${hasTwoPerson}, 1-person=${hasOnePerson}, indep+supervision=${hasIndependentSupervision}, chains=${hasSupersessionChains}`,
      };
    },
  };
}

/**
 * Test 4: Preserve unresolved contradictions
 */
function createContradictionPreservationTest(): BenchmarkCase {
  return {
    id: "bench_04_contradiction_preservation",
    requirement: "preserve_unresolved_contradictions",
    description: "Contradictions between sources preserved, not forcibly resolved",
    input: createContradictionInput(),
    validate: (result) => {
      const { care_state } = result;
      const mobility = care_state.domains.find((d) => d.domain === "physical" && d.subdomain === "mobility");
      if (!mobility) return { pass: false, details: "Mobility domain not found" };

      // Should have preserved contradictions
      const hasContradictions = mobility.contradictions.length > 0;
      const hasContextualVariance = mobility.contextual_values.some((c) =>
        c.context.location === "home"
      ) && mobility.contextual_values.some((c) =>
        c.context.location !== "home"
      );

      // Open loop for contradiction
      const hasOpenLoop = care_state.open_loops.some((ol) =>
        ol.question.toLowerCase().includes("contradiction") ||
        ol.question.toLowerCase().includes("disagree")
      );

      return {
        pass: hasContradictions && hasContextualVariance && hasOpenLoop,
        details: `Contradictions: ${mobility.contradictions.length}, Context variance: ${hasContextualVariance}, Open loop: ${hasOpenLoop}`,
      };
    },
  };
}

/**
 * Test 5: Propagate uncertainty
 */
function createUncertaintyPropagationTest(): BenchmarkCase {
  return {
    id: "bench_05_uncertainty_propagation",
    requirement: "propagate_uncertainty",
    description: "Uncertainty from vague evidence propagated to state",
    input: createUncertaintyInput(),
    validate: (result) => {
      const { care_state } = result;
      const nutrition = care_state.domains.find((d) => d.domain === "physical" && d.subdomain === "appetite");
      if (!nutrition) return { pass: false, details: "Appetite domain not found" };

      // Should have medium/high uncertainty
      const hasUncertainty = ["medium", "high", "unknown"].includes(nutrition.uncertainty);
      const hasUncertaintyNarrative = nutrition.uncertainty_narrative.length > 20;
      const narrativeMentionsMagnitude = nutrition.uncertainty_narrative.toLowerCase().includes("magnitude") ||
        nutrition.uncertainty_narrative.toLowerCase().includes("quantity") ||
        nutrition.uncertainty_narrative.toLowerCase().includes("not established");

      return {
        pass: hasUncertainty && hasUncertaintyNarrative && narrativeMentionsMagnitude,
        details: `Uncertainty: ${nutrition.uncertainty}, Narrative: ${nutrition.uncertainty_narrative.slice(0, 100)}`,
      };
    },
  };
}

/**
 * Test 6: Preserve context-specific states
 */
function createContextPreservationTest(): BenchmarkCase {
  return {
    id: "bench_06_context_preservation",
    requirement: "preserve_context_specific_states",
    description: "Context-specific states (indoors vs outdoors) not flattened",
    input: createContextInput(),
    validate: (result) => {
      const { care_state } = result;
      const mobility = care_state.domains.find((d) => d.domain === "physical" && d.subdomain === "mobility");
      if (!mobility) return { pass: false, details: "Mobility domain not found" };

      // Should have multiple contextual values
      const indoorCtx = mobility.contextual_values.find((c) => c.context.location === "home" || c.context.location === "indoors");
      const outdoorCtx = mobility.contextual_values.find((c) => c.context.location === "outdoors" || c.context.location === "outside");
      const stairsCtx = mobility.contextual_values.find((c) => c.context.location === "stairs");

      // Values should differ
      const indoorValue = indoorCtx?.value || "";
      const outdoorValue = outdoorCtx?.value || "";
      const stairsValue = stairsCtx?.value || "";

      const hasVariance = (indoorValue && outdoorValue && indoorValue !== outdoorValue) ||
        (indoorValue && stairsValue && indoorValue !== stairsValue);

      return {
        pass: hasVariance && mobility.contextual_values.length >= 2,
        details: `Indoor: ${indoorValue}, Outdoor: ${outdoorValue}, Stairs: ${stairsValue}, Contexts: ${mobility.contextual_values.length}`,
      };
    },
  };
}

/**
 * Test 7: Reconstruct multiple domains independently
 */
function createMultiDomainTest(): BenchmarkCase {
  return {
    id: "bench_07_multi_domain",
    requirement: "reconstruct_multiple_domains_independently",
    description: "Each domain reconstructed independently with own evidence",
    input: createMultiDomainInput(),
    validate: (result) => {
      const { care_state } = result;
      const domainsWithEvidence = care_state.domains.filter((d) => d.evidence_summary.total_claims > 0);
      const domainsWithValues = care_state.domains.filter((d) => d.current_value !== null && d.current_value !== "");

      // Should have multiple independent domains
      const physical = care_state.domains.find((d) => d.domain === "physical");
      const cognitive = care_state.domains.find((d) => d.domain === "cognitive");
      const functional = care_state.domains.find((d) => d.domain === "functional");
      const medication = care_state.domains.find((d) => d.domain === "medication");

      return {
        pass: domainsWithEvidence.length >= 4 && domainsWithValues.length >= 4 &&
          !!physical && !!cognitive && !!functional && !!medication,
        details: `Domains with evidence: ${domainsWithEvidence.length}, With values: ${domainsWithValues.length}`,
      };
    },
  };
}

/**
 * Test 8: Distinguish capability from diagnosis
 */
function createCapabilityVsDiagnosisTest(): BenchmarkCase {
  return {
    id: "bench_08_capability_vs_diagnosis",
    requirement: "distinguish_capability_from_diagnosis",
    description: "Dementia diagnosis doesn't auto-establish functional/cognitive state",
    input: createDiagnosisInput(),
    validate: (result) => {
      const { care_state } = result;
      const cognitive = care_state.domains.find((d) => d.domain === "cognitive");
      const functional = care_state.domains.find((d) => d.domain === "functional");

      if (!cognitive || !functional) return { pass: false, details: "Domains not found" };

      // Should NOT just say "dementia" - should have specific capability observations
      const cognitiveSpecific = cognitive.current_value &&
        !cognitive.current_value.toLowerCase().includes("dementia") &&
        (cognitive.current_value.includes("memory") || cognitive.current_value.includes("orientation"));

      const functionalSpecific = functional.current_value &&
        (functional.current_value.includes("dress") || functional.current_value.includes("feed") ||
         functional.current_value.includes("bathe") || functional.current_value.includes("independ"));

      // No domain should be just the diagnosis
      const noDiagnosisOnly = care_state.domains.every((d) =>
        !d.current_value || !d.current_value.toLowerCase().match(/^dementia|^alzheimer|^diagnosis/)
      );

      return {
        pass: cognitiveSpecific && functionalSpecific && noDiagnosisOnly,
        details: `Cognitive: ${cognitive.current_value}, Functional: ${functional.current_value}, No diagnosis-only: ${noDiagnosisOnly}`,
      };
    },
  };
}

/**
 * Test 9: Reconstruct medication state separately from prescription state
 */
function createMedicationStateTest(): BenchmarkCase {
  return {
    id: "bench_09_medication_state",
    requirement: "reconstruct_medication_state_separately_from_prescription_state",
    description: "Prescribed vs filled vs administered tracked separately",
    input: createMedicationInput(),
    validate: (result) => {
      const { care_state } = result;
      const medication = care_state.domains.find((d) => d.domain === "medication");
      if (!medication) return { pass: false, details: "Medication domain not found" };

      // Should have separate subdomain states
      const subdomains = medication.contextual_values.map((c) =>
        c.supporting_claim_ids[0]?.split("_")[2] || "unknown"
      );
      const uniqueSubdomains = new Set(subdomains);

      const hasPrescribed = subdomains.some((s) => s.includes("prescribed") || s.includes("prescrib"));
      const hasFilled = subdomains.some((s) => s.includes("filled") || s.includes("fill"));
      const hasAdministered = subdomains.some((s) => s.includes("administer") || s.includes("taken"));
      const hasAdherenceUncertainty = subdomains.some((s) => s.includes("adherence") || s.includes("uncertain"));

      return {
        pass: uniqueSubdomains.size >= 3 && hasPrescribed && hasFilled && hasAdministered,
        details: `Subdomains: ${Array.from(uniqueSubdomains).join(", ")}, Prescribed: ${hasPrescribed}, Filled: ${hasFilled}, Administered: ${hasAdministered}, Adherence: ${hasAdherenceUncertainty}`,
      };
    },
  };
}

/**
 * Test 10: Reconstruct caregiver responsibility
 */
function createCaregiverResponsibilityTest(): BenchmarkCase {
  return {
    id: "bench_10_caregiver_responsibility",
    requirement: "reconstruct_caregiver_responsibility",
    description: "Who does what in care network reconstructed",
    input: createCaregiverInput(),
    validate: (result) => {
      const { care_state } = result;
      const careNetwork = care_state.domains.find((d) => d.domain === "care_network");
      if (!careNetwork) return { pass: false, details: "Care network domain not found" };

      // Should identify specific task owners
      const hasMedManager = careNetwork.contextual_values.some((c) =>
        c.value.toLowerCase().includes("medication") && c.value.toLowerCase().includes("daughter")
      );
      const hasApptManager = careNetwork.contextual_values.some((c) =>
        c.value.toLowerCase().includes("appointment") && c.value.toLowerCase().includes("son")
      );

      return {
        pass: hasMedManager && hasApptManager,
        details: `Med manager: ${hasMedManager}, Appt manager: ${hasApptManager}, Value: ${careNetwork.current_value}`,
      };
    },
  };
}

/**
 * Test 11: Avoid snapshot-only reasoning
 */
function createNoSnapshotReasoningTest(): BenchmarkCase {
  return {
    id: "bench_11_no_snapshot",
    requirement: "avoid_snapshot_only_reasoning",
    description: "State reconstruction uses trajectory, not just latest note",
    input: createSnapshotInput(),
    validate: (result) => {
      const { care_state } = result;
      const mobility = care_state.domains.find((d) => d.domain === "physical" && d.subdomain === "mobility");
      if (!mobility) return { pass: false, details: "Mobility domain not found" };

      // Should have trajectory showing progression
      const trajectoryLength = mobility.trajectory.length;
      const hasMultipleTimepoints = trajectoryLength >= 3;

      // Current state should reflect trajectory, not just latest
      const latestNote = mobility.trajectory[mobility.trajectory.length - 1]?.value || "";
      const currentValue = mobility.current_value || "";
      const reflectsTrajectory = currentValue !== latestNote || mobility.trajectory.some((t) => t.is_superseded);

      return {
        pass: hasMultipleTimepoints && reflectsTrajectory,
        details: `Trajectory length: ${trajectoryLength}, Current: ${currentValue}, Latest: ${latestNote}, Reflects trajectory: ${reflectsTrajectory}`,
      };
    },
  };
}

/**
 * Test 12: Preserve improvements
 */
function createImprovementPreservationTest(): BenchmarkCase {
  return {
    id: "bench_12_improvement_preservation",
    requirement: "preserve_improvements",
    description: "Improvements in trajectory preserved, not overwritten by later stable state",
    input: createImprovementInput(),
    validate: (result) => {
      const { care_state } = result;
      const mobility = care_state.domains.find((d) => d.domain === "physical" && d.subdomain === "mobility");
      if (!mobility) return { pass: false, details: "Mobility domain not found" };

      // Should show improvement trajectory
      const trajectory = mobility.trajectory;
      const hasDecline = trajectory.some((t) => t.value.includes("walker") || t.value.includes("hospital"));
      const hasImprovement = trajectory.some((t) => t.value.includes("better") || t.value.includes("improv"));
      const currentReflectsImprovement = mobility.current_value?.includes("improv") ||
        mobility.current_value?.includes("better") ||
        (mobility.current_value?.includes("independent") && mobility.current_value?.includes("supervision"));

      return {
        pass: hasDecline && hasImprovement && currentReflectsImprovement,
        details: `Has decline: ${hasDecline}, Has improvement: ${hasImprovement}, Current reflects: ${currentReflectsImprovement}`,
      };
    },
  };
}

/**
 * Test 13: Identify stable domains
 */
function createStableDomainTest(): BenchmarkCase {
  return {
    id: "bench_13_stable_domains",
    requirement: "identify_stable_domains",
    description: "Domains with consistent evidence marked stable",
    input: createStableDomainInput(),
    validate: (result) => {
      const { care_state } = result;
      const stableDomains = care_state.domains.filter((d) => d.stable);
      const unstableDomains = care_state.domains.filter((d) => !d.stable && d.current_value);

      return {
        pass: stableDomains.length > 0 && unstableDomains.length > 0,
        details: `Stable: ${stableDomains.map((d) => d.subdomain).join(", ")}, Unstable: ${unstableDomains.map((d) => d.subdomain).join(", ")}`,
      };
    },
  };
}

/**
 * Test 14: Avoid inventing state where evidence is insufficient
 */
function createNoInventionTest(): BenchmarkCase {
  return {
    id: "bench_14_no_invention",
    requirement: "avoid_inventing_state_where_evidence_insufficient",
    description: "No state invented for domains without evidence",
    input: createInsufficientEvidenceInput(),
    validate: (result) => {
      const { care_state } = result;
      const cognitive = care_state.domains.find((d) => d.domain === "cognitive" && d.subdomain === "executive_function");

      if (!cognitive) return { pass: false, details: "Executive function domain not found" };

      // Should have insufficient evidence confidence and unknown uncertainty
      const hasInsufficientConfidence = cognitive.confidence === "insufficient_evidence";
      const hasUnknownUncertainty = cognitive.uncertainty === "unknown";
      const noInventedValue = !cognitive.current_value ||
        cognitive.current_value.includes("No evidence") ||
        cognitive.current_value.includes("not established");

      return {
        pass: hasInsufficientConfidence && hasUnknownUncertainty && noInventedValue,
        details: `Confidence: ${cognitive.confidence}, Uncertainty: ${cognitive.uncertainty}, Value: ${cognitive.current_value}`,
      };
    },
  };
}

/**
 * Test 15: Produce traceable state to underlying claims
 */
function createTraceabilityTest(): BenchmarkCase {
  return {
    id: "bench_15_traceability",
    requirement: "produce_traceable_state_to_underlying_claims",
    description: "Every state element traceable to claims to events",
    input: createTraceabilityInput(),
    validate: (result) => {
      const { care_state } = result;

      // Every domain state should have traceability
      const traceabilityComplete = care_state.traceability.length > 0;
      const allDomainsTraced = care_state.domains.every((d) =>
        d.contextual_values.every((c) => c.supporting_claim_ids.length > 0)
      );

      // Traceability map should link to events
      const hasEventLinks = care_state.traceability.every((t) => t.event_ids.length > 0);
      const hasProvenanceLinks = care_state.traceability.every((t) => t.provenance_records.length > 0);

      return {
        pass: traceabilityComplete && allDomainsTraced && hasEventLinks && hasProvenanceLinks,
        details: `Traceability entries: ${care_state.traceability.length}, All traced: ${allDomainsTraced}, Event links: ${hasEventLinks}, Provenance links: ${hasProvenanceLinks}`,
      };
    },
  };
}

// ============================================
// Test Input Factories
// ============================================

function createDistributedEvidenceInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-01-15", "Patient walks independently at home", "user_input"),
    createEvent("evt2", "2024-03-10", "After hospitalization, patient uses walker for all mobility", "document"),
    createEvent("evt3", "2024-04-20", "Daughter reports patient walks without walker indoors", "user_input"),
    createEvent("evt4", "2024-05-15", "PT note recommends walker for outdoor use", "document"),
    createEvent("evt5", "2024-06-10", "Caregiver says walking much better recently", "user_input"),
    createEvent("evt6", "2024-01-20", "Appetite normal, eats three meals daily", "user_input"),
    createEvent("evt7", "2024-06-01", "Appetite reduced for past 4 days, eating half portions", "user_input"),
    createEvent("evt8", "2024-05-01", "Memory impairment noted, forgets recent conversations", "document"),
    createEvent("evt9", "2024-06-05", "New intermittent evening confusion reported", "user_input"),
    createEvent("evt10", "2024-06-01", "New medication started: donepezil 5mg daily", "document"),
    createEvent("evt11", "2024-06-08", "Daughter manages medications, son handles appointments", "user_input"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-15" };
}

function createCurrentVsHistoricalInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-01-15", "Patient walks independently at home and outdoors", "document"),
    createEvent("evt2", "2024-03-10", "Post-hospitalization: patient uses walker for all mobility", "document"),
    createEvent("evt3", "2024-05-01", "PT assessment: walker recommended for community ambulation", "document"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-05-15" };
}

function createSupersessionInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-01-15", "Patient needs two-person assistance to stand from chair", "document"),
    createEvent("evt2", "2024-03-10", "Patient needs one-person assistance to stand", "document"),
    createEvent("evt3", "2024-05-15", "Patient standing independently with supervision only", "document"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-05-20" };
}

function createContradictionInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-06-01", "Daughter says patient walks independently at home", "user_input"),
    createEvent("evt2", "2024-06-02", "Home health nurse notes patient requires assistance for all walking", "document"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-05" };
}

function createUncertaintyInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-06-01", "She's been eating less lately", "user_input"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-05" };
}

function createContextInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-06-01", "Patient walks independently inside the house", "user_input"),
    createEvent("evt2", "2024-06-02", "Patient needs assistance on stairs", "user_input"),
    createEvent("evt3", "2024-06-03", "Patient cannot walk outdoors without walker", "document"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-05" };
}

function createMultiDomainInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-06-01", "Uses walker for outdoor mobility", "user_input"),
    createEvent("evt2", "2024-06-02", "Memory impairment, forgets recent conversations", "document"),
    createEvent("evt3", "2024-06-03", "Needs help dressing, independent feeding", "user_input"),
    createEvent("evt4", "2024-06-04", "New medication started: donepezil 5mg daily", "document"),
    createEvent("evt5", "2024-06-05", "Daughter manages medications", "user_input"),
    createEvent("evt6", "2024-06-06", "Follow-up appointment not yet scheduled", "user_input"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-10" };
}

function createDiagnosisInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-01-15", "Diagnosis: Alzheimer's dementia", "document"),
    createEvent("evt2", "2024-05-01", "Forgets recent conversations, oriented to person only", "user_input"),
    createEvent("evt3", "2024-05-15", "Needs help dressing, can feed independently", "user_input"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-05-20" };
}

function createMedicationInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-06-01", "Doctor prescribed donepezil 5mg daily", "document"),
    createEvent("evt2", "2024-06-02", "Prescription filled at pharmacy", "user_input"),
    createEvent("evt3", "2024-06-05", "Daughter gave morning dose at 8am", "user_input"),
    createEvent("evt4", "2024-06-08", "Not sure if evening dose was taken", "user_input"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-10" };
}

function createCaregiverInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-06-01", "Daughter manages all medications", "user_input"),
    createEvent("evt2", "2024-06-02", "Son handles doctor appointments and transportation", "user_input"),
    createEvent("evt3", "2024-06-03", "Home health aide visits MWF for bathing", "document"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-05" };
}

function createSnapshotInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-01-15", "Walks independently", "document"),
    createEvent("evt2", "2024-03-10", "Uses walker after hospitalization", "document"),
    createEvent("evt3", "2024-04-20", "Walks without walker indoors", "user_input"),
    createEvent("evt4", "2024-05-15", "PT says walker recommended", "document"),
    createEvent("evt5", "2024-06-10", "Caregiver says walking much better", "user_input"),
    createEvent("evt6", "2024-06-15", "No recent mobility info", "user_input"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-20" };
}

function createImprovementInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-01-15", "Walks independently", "document"),
    createEvent("evt2", "2024-03-10", "Uses walker after hospitalization", "document"),
    createEvent("evt3", "2024-04-20", "Daughter says walks without walker indoors", "user_input"),
    createEvent("evt4", "2024-05-15", "PT note: walker recommended", "document"),
    createEvent("evt5", "2024-06-10", "Caregiver says walking much better", "user_input"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-15" };
}

function createStableDomainInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-01-15", "Vision normal with glasses", "document"),
    createEvent("evt2", "2024-03-10", "Vision unchanged with glasses", "document"),
    createEvent("evt3", "2024-05-01", "Vision stable, no changes", "user_input"),
    createEvent("evt4", "2024-05-15", "Memory impairment, getting worse", "document"),
    createEvent("evt5", "2024-06-01", "More forgetful recently", "user_input"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-05" };
}

function createInsufficientEvidenceInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-06-01", "Patient has dementia", "document"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-05" };
}

function createTraceabilityInput(): ReconstructionInput {
  const events: CanonicalCareEvent[] = [
    createEvent("evt1", "2024-06-01", "Uses walker outdoors", "user_input"),
    createEvent("evt2", "2024-06-02", "Needs help dressing", "user_input"),
  ];

  return { care_recipient_id: "person_123", events, as_of: "2024-06-05" };
}

function createEvent(
  id: string,
  date: string,
  text: string,
  source: "user_input" | "document"
): CanonicalCareEvent {
  return {
    id,
    timestamp: new Date(date).toISOString(),
    event_time: { type: "exact", start: date, confidence: 0.9 },
    ingestion_time: new Date(date).toISOString(),
    raw_input: text,
    extracted_type: "observation",
    entities: [],
    attributes: {},
    uncertainty: [],
    source,
    root_event_id: null,
    situation_id: null,
    document_id: null,
    status: "committed",
    integrity: {
      field_confidence: {
        extracted_fact: { extraction: "medium", user_confirmed: false },
        event_time: { extraction: "high", user_confirmed: false },
      },
      sources: ["caregiver_observation"],
      superseded_by_id: null,
      supersedes_id: null,
      original_extraction: null,
      correction_count: 0,
      audit_trail_ids: [],
    },
    priority: {
      score: 50,
      tier: "routine",
      factors: { recency: 0.5, clinical: 0.5, uncertainty: 0.5, caregiver: 0.5, dependency: 0.5 },
      computed_at: new Date().toISOString(),
    },
    source_attribution: { caregiver_id: "cg1", source_type: source, confidence: 0.8 },
  };
}

/**
 * Print benchmark summary
 */
export function printBenchmarkSummary(results: BenchmarkResult[]): void {
  const passed = results.filter((r) => r.passed).length;
  const failed = results.filter((r) => !r.passed).length;

  console.log("\n=== STATE RECONSTRUCTION BENCHMARK RESULTS ===");
  console.log(`Total: ${results.length} | Passed: ${passed} | Failed: ${failed}\n`);

  for (const result of results) {
    const status = result.passed ? "✓ PASS" : "✗ FAIL";
    console.log(`${status} | ${result.requirement}`);
    console.log(`       ${result.details}`);
    console.log(`       Duration: ${result.durationMs}ms\n`);
  }

  console.log("=== FAILURE TAXONOMY COVERAGE ===");
  const failureCodes = new Set<string>();
  // Would collect from result.care_state.failures in real run
  console.log(`Failure codes detected: ${failureCodes.size > 0 ? Array.from(failureCodes).join(", ") : "None (run with full pipeline)"}`);
}