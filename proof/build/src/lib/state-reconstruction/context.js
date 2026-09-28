"use strict";
/**
 * State Reconstruction — Context Preserver
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.extractContextualStates = extractContextualStates;
exports.mergeContextualStates = mergeContextualStates;
exports.hasContextVariance = hasContextVariance;
exports.formatContextualState = formatContextualState;
exports.getContextValuesForDimension = getContextValuesForDimension;
exports.findContextualStates = findContextualStates;
const contract_constants_1 = require("./contract-constants");
/**
 * Extract and preserve context-specific states from claims
 */
function extractContextualStates(claims) {
    const contextualStates = [];
    // Group claims by context signature
    const contextGroups = new Map();
    for (const claim of claims) {
        if (claim.status !== "active")
            continue;
        const contextKey = buildContextKey(claim.context);
        if (!contextGroups.has(contextKey)) {
            contextGroups.set(contextKey, []);
        }
        contextGroups.get(contextKey).push(claim);
    }
    // Build contextual state for each group
    for (const [contextKey, groupClaims] of contextGroups) {
        const context = parseContextKey(contextKey);
        const state = buildContextualState(context, groupClaims);
        if (state) {
            contextualStates.push(state);
        }
    }
    return contextualStates;
}
/**
 * Build context key from context object
 */
function buildContextKey(context) {
    return contract_constants_1.CONTEXT_DIMENSIONS.map((dim) => context[dim] || "unspecified").join("|");
}
/**
 * Parse context key back to object
 */
function parseContextKey(key) {
    const parts = key.split("|");
    const context = {
        location: null,
        activity: null,
        time_of_day: null,
        caregiver_present: null,
        assistive_device: null,
        social_setting: null,
    };
    contract_constants_1.CONTEXT_DIMENSIONS.forEach((dim, i) => {
        const val = parts[i];
        context[dim] = val && val !== "unspecified" ? val : null;
    });
    return context;
}
/**
 * Build contextual state from grouped claims
 */
function buildContextualState(context, claims) {
    if (claims.length === 0)
        return null;
    // Synthesize value from claims
    const value = synthesizeContextualValue(claims);
    // Collect supporting and contradicting claim IDs
    const supporting = claims.filter((c) => c.status === "active").map((c) => c.id);
    const contradicting = claims.filter((c) => c.status === "contradicted").map((c) => c.id);
    // Determine uncertainty
    const uncertainties = claims.map((c) => c.uncertainty);
    const aggregated = aggregateUncertainty(uncertainties);
    return {
        context,
        value,
        uncertainty: aggregated,
        supporting_claim_ids: supporting,
        contradicting_claim_ids: contradicting,
    };
}
/**
 * Synthesize a single value from multiple claims in same context
 */
function synthesizeContextualValue(claims) {
    if (claims.length === 1)
        return claims[0].statement;
    // Group similar statements
    const statements = claims.map((c) => c.statement);
    const clusters = clusterStatements(statements);
    // Take the most recent cluster's representative
    const latestCluster = clusters.reduce((latest, cluster) => {
        const clusterTime = Math.max(...cluster.claims.map((c) => new Date(c.valid_from).getTime()));
        const latestTime = Math.max(...latest.claims.map((c) => new Date(c.valid_from).getTime()));
        return clusterTime > latestTime ? cluster : latest;
    });
    return latestCluster.representative;
}
/**
 * Cluster similar statements
 */
function clusterStatements(statements) {
    // Simple clustering by keyword overlap
    const clusters = [];
    for (const stmt of statements) {
        let matched = false;
        for (const cluster of clusters) {
            if (calculateSimilarity(stmt, cluster.representative) > 0.6) {
                cluster.claims.push(stmt);
                matched = true;
                break;
            }
        }
        if (!matched) {
            clusters.push({ representative: stmt, claims: [stmt] });
        }
    }
    return clusters.map((c) => ({ representative: c.representative, claims: [] }));
}
/**
 * Calculate similarity between two strings
 */
function calculateSimilarity(a, b) {
    const wordsA = new Set(a.toLowerCase().split(/\W+/).filter((w) => w.length > 2));
    const wordsB = new Set(b.toLowerCase().split(/\W+/).filter((w) => w.length > 2));
    const intersection = new Set([...wordsA].filter((w) => wordsB.has(w)));
    const union = new Set([...wordsA, ...wordsB]);
    return union.size > 0 ? intersection.size / union.size : 0;
}
/**
 * Aggregate uncertainty levels
 */
function aggregateUncertainty(levels) {
    const order = { none: 0, low: 1, medium: 2, high: 3, unknown: 4 };
    if (levels.includes("unknown"))
        return "unknown";
    if (levels.includes("high"))
        return "high";
    const mediumCount = levels.filter((l) => l === "medium").length;
    if (mediumCount >= levels.length / 2)
        return "medium";
    if (levels.includes("low"))
        return "low";
    return "none";
}
/**
 * Merge contextual states into domain state
 */
function mergeContextualStates(domainState, contextualStates) {
    // Determine primary current value from most recent/context-rich contextual state
    const primaryContext = selectPrimaryContext(contextualStates);
    const currentValue = primaryContext?.value || null;
    // Build trajectory from all claims
    const trajectory = buildTrajectoryFromContextualStates(contextualStates);
    // Collect all contradictions
    const contradictions = collectContradictions(contextualStates);
    return {
        ...domainState,
        current_value: currentValue,
        contextual_values: contextualStates,
        trajectory,
        contradictions,
    };
}
/**
 * Select primary context for current value
 */
function selectPrimaryContext(contextualStates) {
    if (contextualStates.length === 0)
        return null;
    // Prefer contexts with:
    // 1. Most supporting claims
    // 2. Lowest uncertainty
    // 3. Most specific context (most dimensions specified)
    // 4. Most recent evidence
    return contextualStates.reduce((best, current) => {
        const bestScore = scoreContext(best);
        const currentScore = scoreContext(current);
        return currentScore > bestScore ? current : best;
    });
}
/**
 * Score a contextual state for primary selection
 */
function scoreContext(ctx) {
    let score = 0;
    // Supporting claims count
    score += ctx.supporting_claim_ids.length * 10;
    // Uncertainty penalty
    const uncOrder = { none: 0, low: 1, medium: 2, high: 3, unknown: 4 };
    score -= uncOrder[ctx.uncertainty] * 15;
    // Context specificity (more specified dimensions = higher score)
    const specifiedDims = Object.values(ctx.context).filter((v) => v !== null).length;
    score += specifiedDims * 5;
    // Contradiction penalty
    score -= ctx.contradicting_claim_ids.length * 20;
    return score;
}
/**
 * Build trajectory from contextual states
 */
function buildTrajectoryFromContextualStates(contextualStates) {
    // Flatten all claims with timestamps
    const allPoints = [];
    for (const ctx of contextualStates) {
        for (const claimId of ctx.supporting_claim_ids) {
            allPoints.push({
                timestamp: new Date().toISOString(), // Would need claim lookup for actual time
                value: ctx.value,
                claim_ids: [claimId],
                context: ctx.context,
            });
        }
    }
    // Sort by timestamp and group similar values
    return allPoints.map((p, i) => ({
        timestamp: p.timestamp,
        value: p.value,
        claim_ids: p.claim_ids,
        temporal_status: "historical",
        is_superseded: i < allPoints.length - 1,
        supersession_note: i < allPoints.length - 1 ? "Superseded by later evidence" : null,
    }));
}
/**
 * Collect contradictions from contextual states
 */
function collectContradictions(contextualStates) {
    // This would be populated from the contradiction detection phase
    // For now, return empty - actual contradictions come from contradiction.ts
    return [];
}
/**
 * Check if domain state has meaningful context variance
 */
function hasContextVariance(domainState) {
    if (domainState.contextual_values.length <= 1)
        return false;
    // Check if values differ across contexts
    const values = domainState.contextual_values.map((c) => c.value);
    const uniqueValues = new Set(values);
    return uniqueValues.size > 1;
}
/**
 * Format contextual state for output
 */
function formatContextualState(ctx) {
    const contextParts = [];
    for (const [dim, val] of Object.entries(ctx.context)) {
        if (val)
            contextParts.push(`${dim}: ${val}`);
    }
    const contextStr = contextParts.length > 0 ? ` (${contextParts.join(", ")})` : "";
    const uncStr = ctx.uncertainty !== "none" ? ` [${ctx.uncertainty} uncertainty]` : "";
    return `${ctx.value}${contextStr}${uncStr}`;
}
/**
 * Get all unique context values for a dimension across domain
 */
function getContextValuesForDimension(domainState, dimension) {
    const values = new Set();
    for (const ctx of domainState.contextual_values) {
        const val = ctx.context[dimension];
        if (val)
            values.add(val);
    }
    return Array.from(values);
}
/**
 * Find contextual states matching a context query
 */
function findContextualStates(domainState, query) {
    return domainState.contextual_values.filter((ctx) => {
        for (const [dim, val] of Object.entries(query)) {
            if (val && ctx.context[dim] !== val) {
                return false;
            }
        }
        return true;
    });
}
