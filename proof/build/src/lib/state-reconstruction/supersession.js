"use strict";
/**
 * State Reconstruction — Supersession Handler
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.detectSupersession = detectSupersession;
exports.applySupersession = applySupersession;
exports.getCurrentClaim = getCurrentClaim;
exports.getClaimTrajectory = getClaimTrajectory;
exports.validateSupersessionChain = validateSupersessionChain;
exports.mergeSupersessionChains = mergeSupersessionChains;
const contract_constants_1 = require("./contract-constants");
/**
 * Detect supersession relationships between claims
 */
function detectSupersession(claims) {
    const chains = new Map();
    const sortedClaims = [...claims].sort((a, b) => {
        const timeA = new Date(a.valid_from).getTime();
        const timeB = new Date(b.valid_from).getTime();
        return timeA - timeB;
    });
    // Group claims by domain + subdomain
    const grouped = new Map();
    for (const claim of sortedClaims) {
        const key = `${claim.domain}:${claim.subdomain}`;
        if (!grouped.has(key))
            grouped.set(key, []);
        grouped.get(key).push(claim);
    }
    // Process each group for supersession
    for (const [key, groupClaims] of grouped) {
        const chain = buildSupersessionChain(groupClaims);
        if (chain.chain.length > 0) {
            chains.set(key, chain);
        }
    }
    return Array.from(chains.values());
}
/**
 * Build supersession chain for a group of claims
 */
function buildSupersessionChain(claims) {
    if (claims.length <= 1) {
        return {
            original_claim_id: claims[0]?.id || "",
            chain: [],
            current_claim_id: claims[0]?.id || null,
        };
    }
    const chain = [];
    let currentClaim = claims[0];
    for (let i = 1; i < claims.length; i++) {
        const nextClaim = claims[i];
        const relation = determineSupersessionRelation(currentClaim, nextClaim);
        if (relation !== "none") {
            chain.push({
                from_claim_id: currentClaim.id,
                to_claim_id: nextClaim.id,
                relation,
                timestamp: nextClaim.valid_from,
                reason: generateSupersessionReason(currentClaim, nextClaim, relation),
            });
            // Update claim statuses
            currentClaim.status = "superseded";
            currentClaim.superseded_by = nextClaim.id;
            currentClaim.supersession_relation = relation;
            nextClaim.status = "active";
            currentClaim = nextClaim;
        }
    }
    return {
        original_claim_id: claims[0].id,
        chain,
        current_claim_id: currentClaim.id,
    };
}
/**
 * Determine if and how one claim supersedes another
 */
function determineSupersessionRelation(prior, current) {
    // Must be same domain and subdomain
    if (prior.domain !== current.domain || prior.subdomain !== current.subdomain) {
        return "none";
    }
    // Current must be later
    const priorTime = new Date(prior.valid_from).getTime();
    const currentTime = new Date(current.valid_from).getTime();
    if (currentTime <= priorTime)
        return "none";
    // Check for explicit supersession indicators
    const currentText = current.statement.toLowerCase();
    const priorText = prior.statement.toLowerCase();
    // Direct replacement - explicit correction
    if (currentText.includes("correct") || currentText.includes("wrong") ||
        currentText.includes("actually") || currentText.includes("not ")) {
        return "correction";
    }
    // Refinement - more specific detail
    if (currentText.length > priorText.length * 1.5 &&
        currentText.includes(priorText.substring(0, Math.min(20, priorText.length)))) {
        return "refinement";
    }
    // Expansion - adds new information
    if (currentText.includes("also") || currentText.includes("additionally") ||
        currentText.includes("plus") || currentText.includes("and")) {
        return "expansion";
    }
    // Contraction - narrows scope
    if (currentText.includes("only") || currentText.includes("just") ||
        currentText.length < priorText.length * 0.7) {
        return "contraction";
    }
    // Default: direct replacement for same domain/subdomain with later timestamp
    return "direct_replacement";
}
/**
 * Generate human-readable reason for supersession
 */
function generateSupersessionReason(prior, current, relation) {
    const reasons = {
        direct_replacement: "Later observation replaces earlier one",
        refinement: "More detailed observation refines earlier understanding",
        correction: "Explicit correction of prior claim",
        expansion: "Additional information expands prior claim",
        contraction: "Scope narrowed from prior claim",
    };
    return reasons[relation];
}
/**
 * Apply supersession to filter claims to current active set
 */
function applySupersession(claims, chains) {
    const supersededIds = new Set();
    for (const chain of chains) {
        for (const link of chain.chain) {
            supersededIds.add(link.from_claim_id);
        }
    }
    return claims.filter((c) => !supersededIds.has(c.id));
}
/**
 * Get current claim for a domain/subdomain from chain
 */
function getCurrentClaim(chain, allClaims) {
    if (!chain.current_claim_id)
        return null;
    return allClaims.find((c) => c.id === chain.current_claim_id) || null;
}
/**
 * Get full trajectory for a domain/subdomain
 */
function getClaimTrajectory(chain, allClaims) {
    const trajectory = [];
    // Add original
    const original = allClaims.find((c) => c.id === chain.original_claim_id);
    if (original) {
        trajectory.push({ claim: original, is_current: false });
    }
    // Add each link
    for (const link of chain.chain) {
        const claim = allClaims.find((c) => c.id === link.to_claim_id);
        if (claim) {
            trajectory.push({ claim, is_current: link.to_claim_id === chain.current_claim_id });
        }
    }
    return trajectory;
}
/**
 * Validate supersession chain integrity
 */
function validateSupersessionChain(chain) {
    const issues = [];
    if (!chain.original_claim_id) {
        issues.push("Missing original claim ID");
    }
    for (let i = 0; i < chain.chain.length; i++) {
        const link = chain.chain[i];
        if (!contract_constants_1.SUPERSESSION_RELATIONS.includes(link.relation)) {
            issues.push(`Invalid supersession relation: ${link.relation}`);
        }
        if (i > 0) {
            const prevLink = chain.chain[i - 1];
            if (prevLink.to_claim_id !== link.from_claim_id) {
                issues.push(`Chain broken at link ${i}: ${prevLink.to_claim_id} !== ${link.from_claim_id}`);
            }
        }
    }
    if (chain.current_claim_id) {
        const lastLink = chain.chain[chain.chain.length - 1];
        if (lastLink && lastLink.to_claim_id !== chain.current_claim_id) {
            issues.push("Current claim ID doesn't match chain end");
        }
    }
    return { valid: issues.length === 0, issues };
}
/**
 * Merge supersession chains when new claims arrive
 */
function mergeSupersessionChains(existingChains, newClaims) {
    const merged = new Map();
    // Load existing chains
    for (const chain of existingChains) {
        merged.set(`${chain.original_claim_id}`, chain);
    }
    // Group new claims by domain/subdomain
    const newGroups = new Map();
    for (const claim of newClaims) {
        const key = `${claim.domain}:${claim.subdomain}`;
        if (!newGroups.has(key))
            newGroups.set(key, []);
        newGroups.get(key).push(claim);
    }
    // Merge each group
    for (const [key, claims] of newGroups) {
        const existingChain = Array.from(merged.values()).find((c) => c.original_claim_id === claims[0]?.id || c.chain.some((l) => l.to_claim_id === claims[0]?.id));
        if (existingChain) {
            // Extend existing chain
            const lastClaimId = existingChain.current_claim_id || existingChain.original_claim_id;
            const lastClaim = claims.find((c) => c.id === lastClaimId);
            if (lastClaim) {
                const newChain = buildSupersessionChain([lastClaim, ...claims.filter((c) => c.id !== lastClaim.id)]);
                // Append to existing
                for (const link of newChain.chain) {
                    existingChain.chain.push(link);
                }
                existingChain.current_claim_id = newChain.current_claim_id;
            }
        }
        else {
            // New chain
            const newChain = buildSupersessionChain(claims);
            merged.set(key, newChain);
        }
    }
    return Array.from(merged.values());
}
