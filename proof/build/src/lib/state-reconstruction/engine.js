"use strict";
/**
 * State Reconstruction — Main Engine
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.stateReconstructionEngine = exports.StateReconstructionEngine = void 0;
exports.reconstructCareState = reconstructCareState;
const evidence_graph_1 = require("./evidence-graph");
const supersession_1 = require("./supersession");
const contradiction_1 = require("./contradiction");
const context_1 = require("./context");
const domain_reconstruction_1 = require("./domain-reconstruction");
/**
 * Main state reconstruction engine
 */
class StateReconstructionEngine {
    previousState = null;
    /**
     * Reconstruct current care state from evidence
     */
    async reconstruct(input) {
        const startTime = Date.now();
        const stagesCompleted = [];
        // Stage 1: Evidence Graph Construction
        const evidenceGraph = (0, evidence_graph_1.buildEvidenceGraph)(input);
        stagesCompleted.push("evidence_graph_construction");
        // Stage 2: Entity Resolution (already done in buildEvidenceGraph)
        stagesCompleted.push("entity_resolution");
        // Stage 3: Claim Aggregation (already done in buildEvidenceGraph)
        stagesCompleted.push("claim_aggregation");
        // Stage 4: Provenance Binding
        const claimsWithProvenance = this.bindProvenance(evidenceGraph);
        stagesCompleted.push("provenance_binding");
        // Stage 5: Supersession Resolution
        const supersessionChains = (0, supersession_1.detectSupersession)(claimsWithProvenance);
        const activeClaims = (0, supersession_1.applySupersession)(claimsWithProvenance, supersessionChains);
        stagesCompleted.push("supersession_resolution");
        // Stage 6: Temporal Validity Filtering
        const temporallyValidClaims = this.filterTemporalValidity(activeClaims, input.as_of);
        stagesCompleted.push("temporal_validity_filtering");
        // Stage 7: Uncertainty Propagation (done per-domain)
        stagesCompleted.push("uncertainty_propagation");
        // Stage 8: Contradiction Detection & Preservation
        const contradictionSets = (0, contradiction_1.detectContradictions)(temporallyValidClaims, evidenceGraph.entities);
        const allContradictions = contradictionSets.flatMap((cs) => cs.claims.map((claimId) => ({
            id: `contradiction_${claimId}`,
            type: cs.type,
            claim_a_id: claimId,
            claim_b_id: cs.claims.find((id) => id !== claimId) || "",
            claim_a_summary: "",
            claim_b_summary: "",
            context_a: {},
            context_b: {},
            resolution_attempted: false,
            resolution_note: "",
            preserved: cs.preserved,
        })));
        stagesCompleted.push("contradiction_preservation");
        // Stage 9: Context Preservation (done per-domain)
        stagesCompleted.push("context_preservation");
        // Stage 10: Domain Reconstruction
        const domainStates = (0, domain_reconstruction_1.reconstructAllDomains)(temporallyValidClaims, allContradictions);
        stagesCompleted.push("domain_reconstruction");
        // Stage 11: State Assembly
        const careState = this.assembleCareState(input.care_recipient_id, input.as_of || new Date().toISOString(), domainStates, temporallyValidClaims, allContradictions, evidenceGraph, supersessionChains, contradictionSets);
        stagesCompleted.push("state_assembly");
        // Stage 12: Traceability Binding
        const traceability = this.buildTraceabilityMap(careState, temporallyValidClaims, evidenceGraph);
        stagesCompleted.push("traceability_binding");
        // Detect changes from previous state
        const changes = this.detectChanges(careState);
        // Update previous state
        this.previousState = careState;
        const metadata = {
            total_events_processed: input.events.length,
            total_claims_extracted: claimsWithProvenance.length,
            claims_after_supersession: activeClaims.length,
            contradictions_preserved: contradictionSets.filter((cs) => cs.preserved).length,
            uncertainty_propagated: domainStates.reduce((sum, d) => sum + (d.uncertainty === "high" || d.uncertainty === "unknown" ? 1 : 0), 0),
            contexts_preserved: domainStates.reduce((sum, d) => sum + d.contextual_values.length, 0),
            domains_reconstructed: domainStates.length,
            traceability_completeness: traceability.length / Math.max(1, careState.domains.length),
            duration_ms: Date.now() - startTime,
        };
        return {
            care_state: careState,
            evidence_graph: evidenceGraph,
            stages_completed: stagesCompleted,
            reconstruction_metadata: metadata,
        };
    }
    /**
     * Bind provenance to claims
     */
    bindProvenance(evidenceGraph) {
        const claims = [];
        for (const node of evidenceGraph.nodes) {
            for (const claim of node.claims) {
                // Enhance provenance chain
                claim.provenance_chain = [
                    ...claim.provenance_chain,
                    node.event_id,
                    `ingestion:${node.ingestion_time}`,
                ];
                claims.push(claim);
            }
        }
        return claims;
    }
    /**
     * Filter claims by temporal validity
     */
    filterTemporalValidity(claims, asOf) {
        const cutoff = asOf ? new Date(asOf).getTime() : Date.now();
        return claims.filter((claim) => {
            const validFrom = new Date(claim.valid_from).getTime();
            const validUntil = claim.valid_until ? new Date(claim.valid_until).getTime() : Number.MAX_SAFE_INTEGER;
            return validFrom <= cutoff && validUntil >= cutoff;
        });
    }
    /**
     * Assemble complete care state
     */
    assembleCareState(careRecipientId, asOf, domainStates, claims, contradictions, evidenceGraph, supersessionChains, contradictionSets) {
        // Generate open loops
        const openLoops = this.generateOpenLoops(domainStates, claims, contradictionSets);
        // Calculate overall confidence
        const overallConfidence = this.calculateOverallConfidence(domainStates);
        // Collect failures
        const failures = this.detectFailures(domainStates, claims, contradictions, supersessionChains);
        // Build traceability
        const traceability = this.buildTraceabilityMap({ domains: domainStates }, claims, evidenceGraph);
        return {
            care_recipient_id: careRecipientId,
            reconstructed_at: new Date().toISOString(),
            as_of: asOf,
            domains: domainStates,
            open_loops: openLoops,
            overall_confidence: overallConfidence,
            traceability,
            failures,
        };
    }
    /**
     * Generate open loops from unresolved issues
     */
    generateOpenLoops(domainStates, claims, contradictionSets) {
        const openLoops = [];
        // From high uncertainty domains
        for (const domainState of domainStates) {
            if (domainState.uncertainty === "high" || domainState.uncertainty === "unknown") {
                openLoops.push({
                    id: `openloop_${domainState.domain}_${domainState.subdomain}_${Date.now()}`,
                    domain: domainState.domain,
                    subdomain: domainState.subdomain,
                    question: `Current ${domainState.subdomain} state not reliably established`,
                    why_it_matters: `Cannot determine care needs for ${domainState.subdomain} without clearer evidence`,
                    related_claim_ids: domainState.contextual_values.flatMap((c) => c.supporting_claim_ids),
                    blocking: ["care_planning", "decision_making"],
                    first_noted: domainState.last_updated,
                    last_updated: new Date().toISOString(),
                });
            }
        }
        // From preserved contradictions
        for (const cs of contradictionSets) {
            if (cs.preserved) {
                openLoops.push({
                    id: `openloop_contradiction_${cs.id}`,
                    domain: cs.claims[0] ? (claims.find((c) => c.id === cs.claims[0])?.domain || "unknown") : "unknown",
                    subdomain: cs.claims[0] ? (claims.find((c) => c.id === cs.claims[0])?.subdomain || "unknown") : "unknown",
                    question: `Contradiction between sources not resolved: ${cs.type}`,
                    why_it_matters: `Conflicting information affects care decisions`,
                    related_claim_ids: cs.claims,
                    blocking: ["care_planning", "communication"],
                    first_noted: new Date().toISOString(),
                    last_updated: new Date().toISOString(),
                });
            }
        }
        // From evidence gaps
        for (const domainState of domainStates) {
            for (const gap of domainState.evidence_summary.gap_periods) {
                openLoops.push({
                    id: `openloop_gap_${domainState.domain}_${domainState.subdomain}_${Date.now()}`,
                    domain: domainState.domain,
                    subdomain: domainState.subdomain,
                    question: `Evidence gap: ${gap}`,
                    why_it_matters: `Gap in monitoring may miss important changes`,
                    related_claim_ids: [],
                    blocking: ["trend_analysis"],
                    first_noted: new Date().toISOString(),
                    last_updated: new Date().toISOString(),
                });
            }
        }
        return openLoops;
    }
    /**
     * Calculate overall confidence
     */
    calculateOverallConfidence(domainStates) {
        const confidences = domainStates.map((d) => d.confidence);
        const order = {
            well_supported: 4,
            moderately_supported: 3,
            weakly_supported: 2,
            insufficient_evidence: 1,
            contradicted: 0,
        };
        const avg = confidences.reduce((sum, c) => sum + order[c], 0) / confidences.length;
        if (avg >= 3.5)
            return "well_supported";
        if (avg >= 2.5)
            return "moderately_supported";
        if (avg >= 1.5)
            return "weakly_supported";
        if (avg > 0)
            return "insufficient_evidence";
        return "contradicted";
    }
    /**
     * Detect reconstruction failures
     */
    detectFailures(domainStates, claims, contradictions, supersessionChains) {
        const failures = [];
        // Check for snapshot-only reasoning (no historical trajectory)
        const hasTrajectory = domainStates.some((d) => d.trajectory.length > 1);
        if (!hasTrajectory && claims.length > 2) {
            failures.push("SNAPSHOT_STATE_ERROR");
        }
        // Check for latest-note-as-state
        const latestOnly = domainStates.every((d) => d.contextual_values.every((c) => {
            const claim = claims.find((cl) => cl.id === c.supporting_claim_ids[0]);
            return claim && claim.temporal_status === "current";
        }));
        if (latestOnly && claims.some((c) => c.temporal_status !== "current")) {
            failures.push("LATEST_NOTE_AS_STATE");
        }
        // Check for supersession failure
        const hasSupersession = supersessionChains.some((c) => c.chain.length > 0);
        const hasSupersededClaims = claims.some((c) => c.status === "superseded");
        if (hasSupersededClaims && !hasSupersession) {
            failures.push("STATE_SUPERSESSION_FAILURE");
        }
        // Check for contradiction collapse
        const preservedContradictions = contradictions.filter((c) => c.preserved).length;
        const totalContradictions = contradictions.length;
        if (totalContradictions > 0 && preservedContradictions === 0) {
            failures.push("STATE_CONTRADICTION_COLLAPSE");
        }
        // Check for uncertainty loss
        const allLowUncertainty = domainStates.every((d) => d.uncertainty === "none" || d.uncertainty === "low");
        const hasUncertainClaims = claims.some((c) => c.uncertainty === "high" || c.uncertainty === "unknown");
        if (allLowUncertainty && hasUncertainClaims) {
            failures.push("STATE_UNCERTAINTY_LOSS");
        }
        // Check for context loss
        const noContextVariance = domainStates.every((d) => !(0, context_1.hasContextVariance)(d));
        const hasContextClaims = claims.some((c) => Object.values(c.context).some((v) => v !== null));
        if (noContextVariance && hasContextClaims) {
            failures.push("STATE_CONTEXT_LOSS");
        }
        // Check for domain collapse
        const domainsWithEvidence = new Set(claims.map((c) => c.domain));
        const domainsReconstructed = new Set(domainStates.filter((d) => d.current_value).map((d) => d.domain));
        if (domainsWithEvidence.size > domainsReconstructed.size) {
            failures.push("STATE_DOMAIN_COLLAPSE");
        }
        // Check for diagnosis-driven state
        const hasDiagnosisClaims = claims.some((c) => c.statement.toLowerCase().includes("dementia") ||
            c.statement.toLowerCase().includes("alzheimer") ||
            c.statement.toLowerCase().includes("diagnosis"));
        const hasOnlyDiagnosis = domainStates.every((d) => d.current_value?.toLowerCase().includes("dementia") ||
            d.current_value?.toLowerCase().includes("diagnosis"));
        if (hasDiagnosisClaims && hasOnlyDiagnosis) {
            failures.push("STATE_FROM_DIAGNOSIS");
        }
        // Check for stale reconstruction
        const oldestCurrent = Math.min(...claims.filter((c) => c.temporal_status === "current").map((c) => new Date(c.valid_from).getTime()));
        const now = Date.now();
        if (claims.some((c) => c.temporal_status === "current") && (now - oldestCurrent) > 90 * 24 * 60 * 60 * 1000) {
            failures.push("STALE_STATE_RECONSTRUCTION");
        }
        // Check for unsupported current state
        const hasCurrentClaims = claims.some((c) => c.temporal_status === "current");
        const allCurrentWeak = domainStates.every((d) => d.confidence === "insufficient_evidence" || d.confidence === "weakly_supported");
        if (hasCurrentClaims && allCurrentWeak) {
            failures.push("UNSUPPORTED_CURRENT_STATE");
        }
        // Check for provenance loss
        const missingProvenance = claims.some((c) => c.provenance_chain.length === 0);
        if (missingProvenance) {
            failures.push("STATE_PROVENANCE_LOSS");
        }
        return failures;
    }
    /**
     * Build traceability map
     */
    buildTraceabilityMap(careState, claims, evidenceGraph) {
        const traceability = [];
        for (const domainState of careState.domains) {
            for (const ctx of domainState.contextual_values) {
                for (const claimId of ctx.supporting_claim_ids) {
                    const claim = claims.find((c) => c.id === claimId);
                    if (claim) {
                        const node = evidenceGraph.nodes.find((n) => n.event_id === claim.source_event_ids[0]);
                        traceability.push({
                            state_element: `${domainState.domain}.${domainState.subdomain}.${ctx.value.slice(0, 50)}`,
                            claim_ids: [claimId],
                            event_ids: claim.source_event_ids,
                            provenance_records: claim.provenance_chain,
                        });
                    }
                }
            }
        }
        return traceability;
    }
    /**
     * Detect changes from previous state
     */
    detectChanges(currentState) {
        if (!this.previousState)
            return [];
        const changes = [];
        for (const currentDomain of currentState.domains) {
            const priorDomain = this.previousState.domains.find((d) => d.domain === currentDomain.domain && d.subdomain === currentDomain.subdomain);
            if (!priorDomain) {
                // New domain
                changes.push({
                    id: `change_new_${currentDomain.domain}_${currentDomain.subdomain}_${Date.now()}`,
                    domain: currentDomain.domain,
                    subdomain: currentDomain.subdomain,
                    prior_state: null,
                    current_state: currentDomain,
                    change_kind: "new",
                    magnitude: "significant",
                    evidence_ids: currentDomain.contextual_values.flatMap((c) => c.supporting_claim_ids),
                    detected_at: new Date().toISOString(),
                    confidence: currentDomain.confidence,
                });
                continue;
            }
            // Compare values
            const priorValue = priorDomain.current_value;
            const currentValue = currentDomain.current_value;
            if (priorValue !== currentValue) {
                let changeKind = "uncertainty_change";
                let magnitude = "minimal";
                if (!priorValue && currentValue) {
                    changeKind = "new";
                    magnitude = "significant";
                }
                else if (priorValue && !currentValue) {
                    changeKind = "resolved";
                    magnitude = "significant";
                }
                else if (currentDomain.uncertainty !== priorDomain.uncertainty) {
                    const uncOrder = { none: 0, low: 1, medium: 2, high: 3, unknown: 4 };
                    if (uncOrder[currentDomain.uncertainty] > uncOrder[priorDomain.uncertainty]) {
                        changeKind = "uncertainty_change";
                        magnitude = "moderate";
                    }
                }
                else {
                    // Check for improvement/decline keywords
                    const currentLower = currentValue?.toLowerCase() || "";
                    const priorLower = priorValue?.toLowerCase() || "";
                    if (currentLower.includes("improv") || currentLower.includes("better") || currentLower.includes("independ")) {
                        changeKind = "improvement";
                        magnitude = "significant";
                    }
                    else if (currentLower.includes("worse") || currentLower.includes("declin") || currentLower.includes("assist")) {
                        changeKind = "decline";
                        magnitude = "significant";
                    }
                    else {
                        changeKind = "fluctuation";
                        magnitude = "moderate";
                    }
                }
                changes.push({
                    id: `change_${currentDomain.domain}_${currentDomain.subdomain}_${Date.now()}`,
                    domain: currentDomain.domain,
                    subdomain: currentDomain.subdomain,
                    prior_state: priorDomain,
                    current_state: currentDomain,
                    change_kind: changeKind,
                    magnitude,
                    evidence_ids: currentDomain.contextual_values.flatMap((c) => c.supporting_claim_ids),
                    detected_at: new Date().toISOString(),
                    confidence: currentDomain.confidence,
                });
            }
        }
        return changes;
    }
    /**
     * Generate state change report
     */
    generateStateChangeReport(careState, changes) {
        const stableDomains = careState.domains
            .filter((d) => d.stable && !changes.some((c) => c.domain === d.domain && c.subdomain === d.subdomain))
            .map((d) => d.domain);
        const newOpenLoops = careState.open_loops.filter((ol) => !this.previousState?.open_loops.some((pol) => pol.question === ol.question));
        const resolvedOpenLoops = this.previousState?.open_loops.filter((pol) => !careState.open_loops.some((ol) => ol.question === pol.question)) || [];
        // Determine overall trajectory
        const improvements = changes.filter((c) => c.change_kind === "improvement").length;
        const declines = changes.filter((c) => c.change_kind === "decline").length;
        const newStates = changes.filter((c) => c.change_kind === "new").length;
        let overallTrajectory = "stable";
        if (improvements > declines && improvements > 0)
            overallTrajectory = "improving";
        else if (declines > improvements && declines > 0)
            overallTrajectory = "declining";
        else if (improvements > 0 || declines > 0)
            overallTrajectory = "mixed";
        else if (newStates > 0)
            overallTrajectory = "uncertain";
        return {
            care_recipient_id: careState.care_recipient_id,
            as_of: careState.as_of,
            changes,
            stable_domains: stableDomains,
            new_open_loops: newOpenLoops,
            resolved_open_loops: resolvedOpenLoops,
            overall_trajectory: overallTrajectory,
        };
    }
    /**
     * Reset engine state (for testing)
     */
    reset() {
        this.previousState = null;
    }
}
exports.StateReconstructionEngine = StateReconstructionEngine;
/**
 * Singleton instance
 */
exports.stateReconstructionEngine = new StateReconstructionEngine();
/**
 * Convenience function for reconstruction
 */
async function reconstructCareState(input) {
    return exports.stateReconstructionEngine.reconstruct(input);
}
