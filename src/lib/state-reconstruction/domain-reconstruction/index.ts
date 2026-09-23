/**
 * State Reconstruction — Domain Reconstruction Engines
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */

import type {
  DomainState,
  CareStateDomain,
  Claim,
  ContextualState,
  Contradiction,
  OpenLoop,
  TraceabilityMap,
} from "./types";

import {
  PHYSICAL_SUBDOMAINS,
  COGNITIVE_SUBDOMAINS,
  FUNCTIONAL_SUBDOMAINS,
  MEDICATION_SUBDOMAINS,
  CARE_NETWORK_SUBDOMAINS,
  OPERATIONAL_SUBDOMAINS,
} from "./contract-constants";

import { propagateUncertainty } from "./uncertainty";
import { extractContextualStates, mergeContextualStates, hasContextVariance } from "./context";

/**
 * Base domain reconstructor interface
 */
export interface DomainReconstructor {
  domain: CareStateDomain;
  subdomains: string[];
  reconstruct(
    claims: Claim[],
    contradictions: Contradiction[],
    existingState?: DomainState
  ): DomainState;
}

/**
 * Physical domain reconstructor
 */
export class PhysicalDomainReconstructor implements DomainReconstructor {
  domain: CareStateDomain = "physical";
  subdomains = [...PHYSICAL_SUBDOMAINS];

  reconstruct(
    claims: Claim[],
    contradictions: Contradiction[],
    existingState?: DomainState
  ): DomainState {
    const domainClaims = claims.filter((c) => c.domain === "physical");
    const domainContradictions = contradictions.filter((c) =>
      domainClaims.some((dc) => dc.id === c.claim_a_id || dc.id === c.claim_b_id)
    );

    const domainState: DomainState = {
      domain: "physical",
      subdomain: "physical",
      current_value: null,
      contextual_values: [],
      trajectory: [],
      uncertainty: "unknown",
      uncertainty_narrative: "",
      contradictions: domainContradictions,
      confidence: "insufficient_evidence",
      evidence_summary: { total_claims: 0, active_claims: 0, superseded_claims: 0, contradicted_claims: 0, uncertain_claims: 0, source_diversity: [], earliest_evidence: null, latest_evidence: null, gap_periods: [] },
      stable: false,
      last_updated: new Date().toISOString(),
    };

    // Reconstruct each physical subdomain
    for (const subdomain of this.subdomains) {
      const subClaims = domainClaims.filter((c) => c.subdomain === subdomain);
      if (subClaims.length === 0) continue;

      const subContradictions = domainContradictions.filter((c) =>
        subClaims.some((sc) => sc.id === c.claim_a_id || sc.id === c.claim_b_id)
      );

      const { uncertainty, uncertainty_narrative, confidence, evidence_summary } = propagateUncertainty(subClaims, "physical", subdomain);
      const contextualStates = extractContextualStates(subClaims);

      const subState: DomainState = {
        domain: "physical",
        subdomain,
        current_value: null,
        contextual_values: contextualStates,
        trajectory: [],
        uncertainty,
        uncertainty_narrative,
        contradictions: subContradictions,
        confidence,
        evidence_summary,
        stable: false,
        last_updated: new Date().toISOString(),
      };

      // Merge contextual states
      const merged = mergeContextualStates(subState, contextualStates);
      domainState.contextual_values.push(...merged.contextual_values);
      domainState.contradictions.push(...merged.contradictions);
    }

    // Determine overall current value for physical domain
    domainState.current_value = this.synthesizePhysicalSummary(domainState.contextual_values);
    domainState.trajectory = this.buildPhysicalTrajectory(domainClaims);
    domainState.stable = this.assessPhysicalStability(domainClaims, domainState.trajectory);

    // Overall uncertainty/confidence
    const allSubClaims = domainClaims.filter((c) => c.status === "active");
    const overall = propagateUncertainty(allSubClaims, "physical", "overall");
    domainState.uncertainty = overall.uncertainty;
    domainState.uncertainty_narrative = overall.uncertainty_narrative;
    domainState.confidence = overall.confidence;
    domainState.evidence_summary = overall.evidence_summary;

    return domainState;
  }

  private synthesizePhysicalSummary(contextualStates: ContextualState[]): string {
    if (contextualStates.length === 0) return "No physical state evidence";

    const parts: string[] = [];
    for (const ctx of contextualStates) {
      const subdomain = ctx.supporting_claim_ids[0]?.split("_")[2] || "unknown";
      parts.push(`${subdomain}: ${ctx.value}`);
    }
    return parts.join("; ");
  }

  private buildPhysicalTrajectory(claims: Claim[]): DomainState["trajectory"] {
    return claims
      .filter((c) => c.status !== "invalidated")
      .sort((a, b) => new Date(a.valid_from).getTime() - new Date(b.valid_from).getTime())
      .map((c, i, arr) => ({
        timestamp: c.valid_from,
        value: c.statement,
        claim_ids: [c.id],
        temporal_status: c.temporal_status,
        is_superseded: i < arr.length - 1,
        supersession_note: c.superseded_by ? `Superseded by ${c.superseded_by}` : null,
      }));
  }

  private assessPhysicalStability(claims: Claim[], trajectory: DomainState["trajectory"]): boolean {
    if (trajectory.length < 2) return false;
    const recent = trajectory.slice(-3);
    return recent.every((t) => t.value === recent[0].value) &&
      !claims.some((c) => c.status === "superseded" && (Date.now() - new Date(c.valid_from).getTime()) < 30 * 24 * 60 * 60 * 1000);
  }
}

/**
 * Cognitive domain reconstructor
 */
export class CognitiveDomainReconstructor implements DomainReconstructor {
  domain: CareStateDomain = "cognitive";
  subdomains = [...COGNITIVE_SUBDOMAINS];

  reconstruct(
    claims: Claim[],
    contradictions: Contradiction[],
    existingState?: DomainState
  ): DomainState {
    const domainClaims = claims.filter((c) => c.domain === "cognitive");
    const domainContradictions = contradictions.filter((c) =>
      domainClaims.some((dc) => dc.id === c.claim_a_id || dc.id === c.claim_b_id)
    );

    const domainState: DomainState = {
      domain: "cognitive",
      subdomain: "cognitive",
      current_value: null,
      contextual_values: [],
      trajectory: [],
      uncertainty: "unknown",
      uncertainty_narrative: "",
      contradictions: domainContradictions,
      confidence: "insufficient_evidence",
      evidence_summary: { total_claims: 0, active_claims: 0, superseded_claims: 0, contradicted_claims: 0, uncertain_claims: 0, source_diversity: [], earliest_evidence: null, latest_evidence: null, gap_periods: [] },
      stable: false,
      last_updated: new Date().toISOString(),
    };

    for (const subdomain of this.subdomains) {
      const subClaims = domainClaims.filter((c) => c.subdomain === subdomain);
      if (subClaims.length === 0) continue;

      const subContradictions = domainContradictions.filter((c) =>
        subClaims.some((sc) => sc.id === c.claim_a_id || sc.id === c.claim_b_id)
      );

      const { uncertainty, uncertainty_narrative, confidence, evidence_summary } = propagateUncertainty(subClaims, "cognitive", subdomain);
      const contextualStates = extractContextualStates(subClaims);

      const subState: DomainState = {
        domain: "cognitive",
        subdomain,
        current_value: null,
        contextual_values: contextualStates,
        trajectory: [],
        uncertainty,
        uncertainty_narrative,
        contradictions: subContradictions,
        confidence,
        evidence_summary,
        stable: false,
        last_updated: new Date().toISOString(),
      };

      const merged = mergeContextualStates(subState, contextualStates);
      domainState.contextual_values.push(...merged.contextual_values);
      domainState.contradictions.push(...merged.contradictions);
    }

    domainState.current_value = this.synthesizeCognitiveSummary(domainState.contextual_values);
    domainState.trajectory = this.buildCognitiveTrajectory(domainClaims);
    domainState.stable = this.assessCognitiveStability(domainClaims);

    const allSubClaims = domainClaims.filter((c) => c.status === "active");
    const overall = propagateUncertainty(allSubClaims, "cognitive", "overall");
    domainState.uncertainty = overall.uncertainty;
    domainState.uncertainty_narrative = overall.uncertainty_narrative;
    domainState.confidence = overall.confidence;
    domainState.evidence_summary = overall.evidence_summary;

    return domainState;
  }

  private synthesizeCognitiveSummary(contextualStates: ContextualState[]): string {
    if (contextualStates.length === 0) return "No cognitive state evidence";

    const parts: string[] = [];
    for (const ctx of contextualStates) {
      const subdomain = ctx.supporting_claim_ids[0]?.split("_")[2] || "unknown";
      parts.push(`${subdomain}: ${ctx.value}`);
    }
    return parts.join("; ");
  }

  private buildCognitiveTrajectory(claims: Claim[]): DomainState["trajectory"] {
    return claims
      .filter((c) => c.status !== "invalidated")
      .sort((a, b) => new Date(a.valid_from).getTime() - new Date(b.valid_from).getTime())
      .map((c, i, arr) => ({
        timestamp: c.valid_from,
        value: c.statement,
        claim_ids: [c.id],
        temporal_status: c.temporal_status,
        is_superseded: i < arr.length - 1,
        supersession_note: c.superseded_by ? `Superseded by ${c.superseded_by}` : null,
      }));
  }

  private assessCognitiveStability(claims: Claim[]): boolean {
    // Cognitive fluctuations are expected - check for new concerning changes
    const recent = claims.filter((c) =>
      c.status === "active" &&
      (Date.now() - new Date(c.valid_from).getTime()) < 14 * 24 * 60 * 60 * 1000
    );
    const hasNewConcern = recent.some((c) =>
      c.statement.toLowerCase().includes("new") ||
      c.statement.toLowerCase().includes("wors") ||
      c.statement.toLowerCase().includes("declin")
    );
    return !hasNewConcern;
  }
}

/**
 * Functional domain reconstructor
 */
export class FunctionalDomainReconstructor implements DomainReconstructor {
  domain: CareStateDomain = "functional";
  subdomains = [...FUNCTIONAL_SUBDOMAINS];

  reconstruct(
    claims: Claim[],
    contradictions: Contradiction[],
    existingState?: DomainState
  ): DomainState {
    const domainClaims = claims.filter((c) => c.domain === "functional");
    const domainContradictions = contradictions.filter((c) =>
      domainClaims.some((dc) => dc.id === c.claim_a_id || dc.id === c.claim_b_id)
    );

    const domainState: DomainState = {
      domain: "functional",
      subdomain: "functional",
      current_value: null,
      contextual_values: [],
      trajectory: [],
      uncertainty: "unknown",
      uncertainty_narrative: "",
      contradictions: domainContradictions,
      confidence: "insufficient_evidence",
      evidence_summary: { total_claims: 0, active_claims: 0, superseded_claims: 0, contradicted_claims: 0, uncertain_claims: 0, source_diversity: [], earliest_evidence: null, latest_evidence: null, gap_periods: [] },
      stable: false,
      last_updated: new Date().toISOString(),
    };

    for (const subdomain of this.subdomains) {
      const subClaims = domainClaims.filter((c) => c.subdomain === subdomain);
      if (subClaims.length === 0) continue;

      const subContradictions = domainContradictions.filter((c) =>
        subClaims.some((sc) => sc.id === c.claim_a_id || sc.id === c.claim_b_id)
      );

      const { uncertainty, uncertainty_narrative, confidence, evidence_summary } = propagateUncertainty(subClaims, "functional", subdomain);
      const contextualStates = extractContextualStates(subClaims);

      // Special handling: functional states are highly context-dependent
      const enrichedContextualStates = this.enrichFunctionalContexts(contextualStates, subClaims);

      const subState: DomainState = {
        domain: "functional",
        subdomain,
        current_value: null,
        contextual_values: enrichedContextualStates,
        trajectory: [],
        uncertainty,
        uncertainty_narrative,
        contradictions: subContradictions,
        confidence,
        evidence_summary,
        stable: false,
        last_updated: new Date().toISOString(),
      };

      const merged = mergeContextualStates(subState, enrichedContextualStates);
      domainState.contextual_values.push(...merged.contextual_values);
      domainState.contradictions.push(...merged.contradictions);
    }

    domainState.current_value = this.synthesizeFunctionalSummary(domainState.contextual_values);
    domainState.trajectory = this.buildFunctionalTrajectory(domainClaims);
    domainState.stable = this.assessFunctionalStability(domainClaims);

    const allSubClaims = domainClaims.filter((c) => c.status === "active");
    const overall = propagateUncertainty(allSubClaims, "functional", "overall");
    domainState.uncertainty = overall.uncertainty;
    domainState.uncertainty_narrative = overall.uncertainty_narrative;
    domainState.confidence = overall.confidence;
    domainState.evidence_summary = overall.evidence_summary;

    return domainState;
  }

  private enrichFunctionalContexts(
    contextualStates: ContextualState[],
    claims: Claim[]
  ): ContextualState[] {
    // Ensure functional states always have location and assistive_device context
    return contextualStates.map((ctx) => {
      const enrichedContext = { ...ctx.context };
      if (!enrichedContext.location) enrichedContext.location = "home";
      if (!enrichedContext.assistive_device) enrichedContext.assistive_device = "none";
      return { ...ctx, context: enrichedContext };
    });
  }

  private synthesizeFunctionalSummary(contextualStates: ContextualState[]): string {
    if (contextualStates.length === 0) return "No functional state evidence";

    // Group by subdomain
    const bySubdomain = new Map<string, ContextualState[]>();
    for (const ctx of contextualStates) {
      const subdomain = ctx.supporting_claim_ids[0]?.split("_")[2] || "unknown";
      if (!bySubdomain.has(subdomain)) bySubdomain.set(subdomain, []);
      bySubdomain.get(subdomain)!.push(ctx);
    }

    const parts: string[] = [];
    for (const [subdomain, states] of bySubdomain) {
      // Show context variance if present
      if (states.length > 1 && hasContextVariance({ contextual_values: states } as DomainState)) {
        const contextParts = states.map((s) => formatContextualState(s)).join(" | ");
        parts.push(`${subdomain}: ${contextParts}`);
      } else {
        parts.push(`${subdomain}: ${states[0].value}`);
      }
    }
    return parts.join("; ");
  }

  private buildFunctionalTrajectory(claims: Claim[]): DomainState["trajectory"] {
    return claims
      .filter((c) => c.status !== "invalidated")
      .sort((a, b) => new Date(a.valid_from).getTime() - new Date(b.valid_from).getTime())
      .map((c, i, arr) => ({
        timestamp: c.valid_from,
        value: c.statement,
        claim_ids: [c.id],
        temporal_status: c.temporal_status,
        is_superseded: i < arr.length - 1,
        supersession_note: c.superseded_by ? `Superseded by ${c.superseded_by}` : null,
      }));
  }

  private assessFunctionalStability(claims: Claim[]): boolean {
    const recentSupersessions = claims.filter((c) =>
      c.status === "superseded" &&
      (Date.now() - new Date(c.valid_from).getTime()) < 30 * 24 * 60 * 60 * 1000
    );
    return recentSupersessions.length === 0;
  }
}

/**
 * Medication domain reconstructor
 */
export class MedicationDomainReconstructor implements DomainReconstructor {
  domain: CareStateDomain = "medication";
  subdomains = [...MEDICATION_SUBDOMAINS];

  reconstruct(
    claims: Claim[],
    contradictions: Contradiction[],
    existingState?: DomainState
  ): DomainState {
    const domainClaims = claims.filter((c) => c.domain === "medication");
    const domainContradictions = contradictions.filter((c) =>
      domainClaims.some((dc) => dc.id === c.claim_a_id || dc.id === c.claim_b_id)
    );

    const domainState: DomainState = {
      domain: "medication",
      subdomain: "medication",
      current_value: null,
      contextual_values: [],
      trajectory: [],
      uncertainty: "unknown",
      uncertainty_narrative: "",
      contradictions: domainContradictions,
      confidence: "insufficient_evidence",
      evidence_summary: { total_claims: 0, active_claims: 0, superseded_claims: 0, contradicted_claims: 0, uncertain_claims: 0, source_diversity: [], earliest_evidence: null, latest_evidence: null, gap_periods: [] },
      stable: false,
      last_updated: new Date().toISOString(),
    };

    // Medication reconstruction is special - we need to track each medication separately
    const medicationGroups = this.groupByMedication(domainClaims);

    for (const [medName, medClaims] of medicationGroups) {
      const subdomain = "prescribed"; // Primary subdomain for medication tracking
      const { uncertainty, uncertainty_narrative, confidence, evidence_summary } = propagateUncertainty(medClaims, "medication", subdomain);
      const contextualStates = extractContextualStates(medClaims);

      const subContradictions = domainContradictions.filter((c) =>
        medClaims.some((sc) => sc.id === c.claim_a_id || sc.id === c.claim_b_id)
      );

      const subState: DomainState = {
        domain: "medication",
        subdomain: medName,
        current_value: null,
        contextual_values: contextualStates,
        trajectory: [],
        uncertainty,
        uncertainty_narrative,
        contradictions: subContradictions,
        confidence,
        evidence_summary,
        stable: false,
        last_updated: new Date().toISOString(),
      };

      const merged = mergeContextualStates(subState, contextualStates);
      domainState.contextual_values.push(...merged.contextual_values);
      domainState.contradictions.push(...merged.contradictions);
    }

    domainState.current_value = this.synthesizeMedicationSummary(domainState.contextual_values);
    domainState.trajectory = this.buildMedicationTrajectory(domainClaims);
    domainState.stable = this.assessMedicationStability(domainClaims);

    const allSubClaims = domainClaims.filter((c) => c.status === "active");
    const overall = propagateUncertainty(allSubClaims, "medication", "overall");
    domainState.uncertainty = overall.uncertainty;
    domainState.uncertainty_narrative = overall.uncertainty_narrative;
    domainState.confidence = overall.confidence;
    domainState.evidence_summary = overall.evidence_summary;

    return domainState;
  }

  private groupByMedication(claims: Claim[]): Map<string, Claim[]> {
    const groups = new Map<string, Claim[]>();

    for (const claim of claims) {
      // Try to extract medication name from statement
      const medName = this.extractMedicationName(claim.statement) || "unspecified_medication";
      if (!groups.has(medName)) groups.set(medName, []);
      groups.get(medName)!.push(claim);
    }

    return groups;
  }

  private extractMedicationName(statement: string): string | null {
    // Simple medication name extraction
    const patterns = [
      /\b(take|taking|on|started|prescribed)\s+(\w+(?:\s+\w+)?)/i,
      /\b(\w+(?:\s+\w+)?)\s+(mg|mcg|ml|tablet|pill|cap)/i,
    ];

    for (const pattern of patterns) {
      const match = statement.match(pattern);
      if (match && match[2]) {
        return match[2].toLowerCase();
      }
      if (match && match[1]) {
        return match[1].toLowerCase();
      }
    }

    return null;
  }

  private synthesizeMedicationSummary(contextualStates: ContextualState[]): string {
    if (contextualStates.length === 0) return "No medication evidence";

    const byMed = new Map<string, ContextualState[]>();
    for (const ctx of contextualStates) {
      const medName = ctx.supporting_claim_ids[0]?.split("_")[2] || "unknown";
      if (!byMed.has(medName)) byMed.set(medName, []);
      byMed.get(medName)!.push(ctx);
    }

    const parts: string[] = [];
    for (const [medName, states] of byMed) {
      const statuses = states.map((s) => s.value).join(", ");
      parts.push(`${medName}: ${statuses}`);
    }
    return parts.join("; ");
  }

  private buildMedicationTrajectory(claims: Claim[]): DomainState["trajectory"] {
    return claims
      .filter((c) => c.status !== "invalidated")
      .sort((a, b) => new Date(a.valid_from).getTime() - new Date(b.valid_from).getTime())
      .map((c, i, arr) => ({
        timestamp: c.valid_from,
        value: c.statement,
        claim_ids: [c.id],
        temporal_status: c.temporal_status,
        is_superseded: i < arr.length - 1,
        supersession_note: c.superseded_by ? `Superseded by ${c.superseded_by}` : null,
      }));
  }

  private assessMedicationStability(claims: Claim[]): boolean {
    // Check for recent medication changes
    const recentChanges = claims.filter((c) =>
      (c.subdomain === "started" || c.subdomain === "discontinued" || c.subdomain === "prescribed") &&
      c.status === "active" &&
      (Date.now() - new Date(c.valid_from).getTime()) < 30 * 24 * 60 * 60 * 1000
    );
    return recentChanges.length === 0;
  }
}

/**
 * Care Network domain reconstructor
 */
export class CareNetworkDomainReconstructor implements DomainReconstructor {
  domain: CareStateDomain = "care_network";
  subdomains = [...CARE_NETWORK_SUBDOMAINS];

  reconstruct(
    claims: Claim[],
    contradictions: Contradiction[],
    existingState?: DomainState
  ): DomainState {
    const domainClaims = claims.filter((c) => c.domain === "care_network");
    const domainContradictions = contradictions.filter((c) =>
      domainClaims.some((dc) => dc.id === c.claim_a_id || dc.id === c.claim_b_id)
    );

    const domainState: DomainState = {
      domain: "care_network",
      subdomain: "care_network",
      current_value: null,
      contextual_values: [],
      trajectory: [],
      uncertainty: "unknown",
      uncertainty_narrative: "",
      contradictions: domainContradictions,
      confidence: "insufficient_evidence",
      evidence_summary: { total_claims: 0, active_claims: 0, superseded_claims: 0, contradicted_claims: 0, uncertain_claims: 0, source_diversity: [], earliest_evidence: null, latest_evidence: null, gap_periods: [] },
      stable: false,
      last_updated: new Date().toISOString(),
    };

    for (const subdomain of this.subdomains) {
      const subClaims = domainClaims.filter((c) => c.subdomain === subdomain);
      if (subClaims.length === 0) continue;

      const subContradictions = domainContradictions.filter((c) =>
        subClaims.some((sc) => sc.id === c.claim_a_id || sc.id === c.claim_b_id)
      );

      const { uncertainty, uncertainty_narrative, confidence, evidence_summary } = propagateUncertainty(subClaims, "care_network", subdomain);
      const contextualStates = extractContextualStates(subClaims);

      const subState: DomainState = {
        domain: "care_network",
        subdomain,
        current_value: null,
        contextual_values: contextualStates,
        trajectory: [],
        uncertainty,
        uncertainty_narrative,
        contradictions: subContradictions,
        confidence,
        evidence_summary,
        stable: false,
        last_updated: new Date().toISOString(),
      };

      const merged = mergeContextualStates(subState, contextualStates);
      domainState.contextual_values.push(...merged.contextual_values);
      domainState.contradictions.push(...merged.contradictions);
    }

    domainState.current_value = this.synthesizeCareNetworkSummary(domainState.contextual_values);
    domainState.trajectory = this.buildCareNetworkTrajectory(domainClaims);
    domainState.stable = this.assessCareNetworkStability(domainClaims);

    const allSubClaims = domainClaims.filter((c) => c.status === "active");
    const overall = propagateUncertainty(allSubClaims, "care_network", "overall");
    domainState.uncertainty = overall.uncertainty;
    domainState.uncertainty_narrative = overall.uncertainty_narrative;
    domainState.confidence = overall.confidence;
    domainState.evidence_summary = overall.evidence_summary;

    return domainState;
  }

  private synthesizeCareNetworkSummary(contextualStates: ContextualState[]): string {
    if (contextualStates.length === 0) return "No care network evidence";

    const parts: string[] = [];
    for (const ctx of contextualStates) {
      const subdomain = ctx.supporting_claim_ids[0]?.split("_")[2] || "unknown";
      parts.push(`${subdomain}: ${ctx.value}`);
    }
    return parts.join("; ");
  }

  private buildCareNetworkTrajectory(claims: Claim[]): DomainState["trajectory"] {
    return claims
      .filter((c) => c.status !== "invalidated")
      .sort((a, b) => new Date(a.valid_from).getTime() - new Date(b.valid_from).getTime())
      .map((c, i, arr) => ({
        timestamp: c.valid_from,
        value: c.statement,
        claim_ids: [c.id],
        temporal_status: c.temporal_status,
        is_superseded: i < arr.length - 1,
        supersession_note: c.superseded_by ? `Superseded by ${c.superseded_by}` : null,
      }));
  }

  private assessCareNetworkStability(claims: Claim[]): boolean {
    const recentChanges = claims.filter((c) =>
      (c.subdomain === "handoffs" || c.subdomain === "task_owners") &&
      c.status === "active" &&
      (Date.now() - new Date(c.valid_from).getTime()) < 30 * 24 * 60 * 60 * 1000
    );
    return recentChanges.length === 0;
  }
}

/**
 * Operational domain reconstructor
 */
export class OperationalDomainReconstructor implements DomainReconstructor {
  domain: CareStateDomain = "operational";
  subdomains = [...OPERATIONAL_SUBDOMAINS];

  reconstruct(
    claims: Claim[],
    contradictions: Contradiction[],
    existingState?: DomainState
  ): DomainState {
    const domainClaims = claims.filter((c) => c.domain === "operational");
    const domainContradictions = contradictions.filter((c) =>
      domainClaims.some((dc) => dc.id === c.claim_a_id || dc.id === c.claim_b_id)
    );

    const domainState: DomainState = {
      domain: "operational",
      subdomain: "operational",
      current_value: null,
      contextual_values: [],
      trajectory: [],
      uncertainty: "unknown",
      uncertainty_narrative: "",
      contradictions: domainContradictions,
      confidence: "insufficient_evidence",
      evidence_summary: { total_claims: 0, active_claims: 0, superseded_claims: 0, contradicted_claims: 0, uncertain_claims: 0, source_diversity: [], earliest_evidence: null, latest_evidence: null, gap_periods: [] },
      stable: false,
      last_updated: new Date().toISOString(),
    };

    for (const subdomain of this.subdomains) {
      const subClaims = domainClaims.filter((c) => c.subdomain === subdomain);
      if (subClaims.length === 0) continue;

      const subContradictions = domainContradictions.filter((c) =>
        subClaims.some((sc) => sc.id === c.claim_a_id || sc.id === c.claim_b_id)
      );

      const { uncertainty, uncertainty_narrative, confidence, evidence_summary } = propagateUncertainty(subClaims, "operational", subdomain);
      const contextualStates = extractContextualStates(subClaims);

      const subState: DomainState = {
        domain: "operational",
        subdomain,
        current_value: null,
        contextual_values: contextualStates,
        trajectory: [],
        uncertainty,
        uncertainty_narrative,
        contradictions: subContradictions,
        confidence,
        evidence_summary,
        stable: false,
        last_updated: new Date().toISOString(),
      };

      const merged = mergeContextualStates(subState, contextualStates);
      domainState.contextual_values.push(...merged.contextual_values);
      domainState.contradictions.push(...merged.contradictions);
    }

    domainState.current_value = this.synthesizeOperationalSummary(domainState.contextual_values);
    domainState.trajectory = this.buildOperationalTrajectory(domainClaims);
    domainState.stable = this.assessOperationalStability(domainClaims);

    const allSubClaims = domainClaims.filter((c) => c.status === "active");
    const overall = propagateUncertainty(allSubClaims, "operational", "overall");
    domainState.uncertainty = overall.uncertainty;
    domainState.uncertainty_narrative = overall.uncertainty_narrative;
    domainState.confidence = overall.confidence;
    domainState.evidence_summary = overall.evidence_summary;

    return domainState;
  }

  private synthesizeOperationalSummary(contextualStates: ContextualState[]): string {
    if (contextualStates.length === 0) return "No operational evidence";

    const parts: string[] = [];
    for (const ctx of contextualStates) {
      const subdomain = ctx.supporting_claim_ids[0]?.split("_")[2] || "unknown";
      parts.push(`${subdomain}: ${ctx.value}`);
    }
    return parts.join("; ");
  }

  private buildOperationalTrajectory(claims: Claim[]): DomainState["trajectory"] {
    return claims
      .filter((c) => c.status !== "invalidated")
      .sort((a, b) => new Date(a.valid_from).getTime() - new Date(b.valid_from).getTime())
      .map((c, i, arr) => ({
        timestamp: c.valid_from,
        value: c.statement,
        claim_ids: [c.id],
        temporal_status: c.temporal_status,
        is_superseded: i < arr.length - 1,
        supersession_note: c.superseded_by ? `Superseded by ${c.superseded_by}` : null,
      }));
  }

  private assessOperationalStability(claims: Claim[]): boolean {
    const openLoops = claims.filter((c) =>
      c.subdomain === "open_loops" && c.status === "active"
    );
    return openLoops.length === 0;
  }
}

/**
 * Get reconstructor for a domain
 */
export function getDomainReconstructor(domain: CareStateDomain): DomainReconstructor {
  switch (domain) {
    case "physical":
      return new PhysicalDomainReconstructor();
    case "cognitive":
      return new CognitiveDomainReconstructor();
    case "functional":
      return new FunctionalDomainReconstructor();
    case "medication":
      return new MedicationDomainReconstructor();
    case "care_network":
      return new CareNetworkDomainReconstructor();
    case "operational":
      return new OperationalDomainReconstructor();
    default:
      throw new Error(`Unknown domain: ${domain}`);
  }
}

/**
 * Reconstruct all domains
 */
export function reconstructAllDomains(
  claims: Claim[],
  contradictions: Contradiction[]
): DomainState[] {
  const domains: CareStateDomain[] = ["physical", "cognitive", "functional", "medication", "care_network", "operational"];
  const results: DomainState[] = [];

  for (const domain of domains) {
    const reconstructor = getDomainReconstructor(domain);
    const state = reconstructor.reconstruct(claims, contradictions);
    results.push(state);
  }

  return results;
}