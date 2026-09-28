"use strict";
/**
 * State Reconstruction — Pipeline Integration
 * SoT: docs/02-product/solenos-state-reconstruction.md
 * Wraps the StateReconstructionEngine for use in SituationEntry pipeline
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.processStateReconstruction = processStateReconstruction;
exports.quickReconstructState = quickReconstructState;
exports.getStateReconstructionEngine = getStateReconstructionEngine;
exports.formatCareStateForCaregiver = formatCareStateForCaregiver;
exports.formatStateChangeReport = formatStateChangeReport;
exports.extractStateInsights = extractStateInsights;
const contract_constants_1 = require("./contract-constants");
const engine_1 = require("./engine");
const benchmark_1 = require("./benchmark");
/**
 * Process State Reconstruction for pipeline
 */
async function processStateReconstruction(input) {
    const asOf = input.as_of ?? new Date().toISOString();
    // Prepare reconstruction input
    const reconstructionInput = {
        care_recipient_id: input.care_recipient_id,
        events: input.all_events,
        as_of: asOf,
        baseline: input.baseline,
        care_reality_profile: input.care_reality_profile,
        continuity_properties: input.continuity_properties,
    };
    // Run reconstruction
    const reconstructionResult = await (0, engine_1.reconstructCareState)(reconstructionInput);
    // Detect state changes
    const stateChangeReport = engine_1.stateReconstructionEngine.generateStateChangeReport(reconstructionResult.care_state, [] // Changes are detected internally
    );
    // Validate
    const validation = (0, benchmark_1.validateReconstructionResult)(reconstructionResult);
    return {
        active: true,
        care_state: reconstructionResult.care_state,
        state_change_report: stateChangeReport,
        reconstruction_result: reconstructionResult,
        validation,
        defining_principle: contract_constants_1.STATE_RECONSTRUCTION_PURPOSE,
        identity: contract_constants_1.STATE_RECONSTRUCTION_IDENTITY,
        benchmark_requirements: contract_constants_1.BENCHMARK_REQUIREMENTS,
        failure_taxonomy: contract_constants_1.FAILURE_TAXONOMY,
    };
}
/**
 * Convenience function for quick state reconstruction
 */
async function quickReconstructState(careRecipientId, events, asOf) {
    const result = await (0, engine_1.reconstructCareState)({
        care_recipient_id: careRecipientId,
        events,
        as_of: asOf,
    });
    return result.care_state;
}
/**
 * Get state reconstruction engine instance (for advanced usage)
 */
function getStateReconstructionEngine() {
    return engine_1.stateReconstructionEngine;
}
/**
 * Format care state for caregiver-facing output
 */
function formatCareStateForCaregiver(careState) {
    const lines = [];
    lines.push("CURRENT CARE STATE");
    lines.push("=".repeat(50));
    for (const domain of careState.domains) {
        if (!domain.current_value && domain.contextual_values.length === 0)
            continue;
        lines.push(`\n${domain.domain.toUpperCase()}`);
        lines.push("-".repeat(domain.domain.length));
        if (domain.current_value) {
            lines.push(`  Overall: ${domain.current_value}`);
        }
        if (domain.contextual_values.length > 1) {
            lines.push("  By Context:");
            for (const ctx of domain.contextual_values) {
                const contextParts = Object.entries(ctx.context)
                    .filter(([, v]) => v)
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(", ");
                const ctxStr = contextParts ? ` (${contextParts})` : "";
                const uncStr = ctx.uncertainty !== "none" ? ` [${ctx.uncertainty}]` : "";
                lines.push(`    • ${ctx.value}${ctxStr}${uncStr}`);
            }
        }
        if (domain.uncertainty_narrative) {
            lines.push(`  Uncertainty: ${domain.uncertainty_narrative}`);
        }
        if (domain.contradictions.length > 0) {
            lines.push(`  Contradictions: ${domain.contradictions.length} preserved`);
        }
        if (domain.stable) {
            lines.push(`  Status: Stable`);
        }
    }
    if (careState.open_loops.length > 0) {
        lines.push("\nOPEN LOOPS");
        lines.push("-".repeat(20));
        for (const loop of careState.open_loops) {
            lines.push(`  • ${loop.question} (${loop.domain}/${loop.subdomain})`);
        }
    }
    lines.push(`\nOverall Confidence: ${careState.overall_confidence}`);
    lines.push(`Reconstructed: ${new Date(careState.reconstructed_at).toLocaleString()}`);
    lines.push(`As of: ${new Date(careState.as_of).toLocaleString()}`);
    if (careState.failures.length > 0) {
        lines.push(`\n⚠ Failures: ${careState.failures.join(", ")}`);
    }
    return lines.join("\n");
}
/**
 * Format state change report for caregiver
 */
function formatStateChangeReport(report) {
    const lines = [];
    lines.push("CARE STATE CHANGES");
    lines.push("=".repeat(50));
    lines.push(`As of: ${new Date(report.as_of).toLocaleString()}`);
    lines.push(`Overall Trajectory: ${report.overall_trajectory.toUpperCase()}`);
    if (report.changes.length > 0) {
        lines.push("\nCHANGES DETECTED:");
        for (const change of report.changes) {
            const magnitude = change.magnitude.toUpperCase();
            const kind = change.change_kind.replace("_", " ").toUpperCase();
            lines.push(`  • [${magnitude}] ${change.domain}/${change.subdomain}: ${kind}`);
            if (change.prior_state) {
                lines.push(`    Prior: ${change.prior_state.current_value}`);
            }
            lines.push(`    Current: ${change.current_state.current_value}`);
            lines.push(`    Confidence: ${change.confidence}`);
        }
    }
    else {
        lines.push("\nNo significant changes detected.");
    }
    if (report.stable_domains.length > 0) {
        lines.push(`\nSTABLE DOMAINS: ${report.stable_domains.join(", ")}`);
    }
    if (report.new_open_loops.length > 0) {
        lines.push("\nNEW OPEN LOOPS:");
        for (const loop of report.new_open_loops) {
            lines.push(`  • ${loop.question}`);
        }
    }
    if (report.resolved_open_loops.length > 0) {
        lines.push("\nRESOLVED OPEN LOOPS:");
        for (const loop of report.resolved_open_loops) {
            lines.push(`  • ${loop.question}`);
        }
    }
    return lines.join("\n");
}
/**
 * Extract key insights for downstream consumers
 */
function extractStateInsights(careState) {
    const criticalChanges = careState.domains
        .filter((d) => d.trajectory.some((t) => t.is_superseded))
        .map((d) => `${d.domain}/${d.subdomain}: ${d.current_value}`);
    const highUncertainty = careState.domains
        .filter((d) => d.uncertainty === "high" || d.uncertainty === "unknown")
        .map((d) => `${d.domain}/${d.subdomain}`);
    const stable = careState.domains
        .filter((d) => d.stable)
        .map((d) => `${d.domain}/${d.subdomain}`);
    const traceabilityScore = careState.traceability.length > 0
        ? Math.min(1, careState.traceability.length / Math.max(1, careState.domains.length))
        : 0;
    return {
        critical_changes: criticalChanges,
        high_uncertainty_domains: highUncertainty,
        stable_domains: stable,
        open_loops_count: careState.open_loops.length,
        traceability_score: Math.round(traceabilityScore * 100),
    };
}
