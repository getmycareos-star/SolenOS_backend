"use strict";
/**
 * State Reconstruction — Contradiction Preserver
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.detectContradictions = detectContradictions;
exports.attemptContradictionResolution = attemptContradictionResolution;
exports.formatContradictionForOutput = formatContradictionForOutput;
const contract_constants_1 = require("./contract-constants");
/**
 * Detect contradictions between claims
 */
function detectContradictions(claims, entities) {
    const contradictionSets = [];
    const processed = new Set();
    // Group claims by domain + subdomain
    const grouped = new Map();
    for (const claim of claims) {
        if (claim.status === "superseded" || claim.status === "invalidated")
            continue;
        const key = `${claim.domain}:${claim.subdomain}`;
        if (!grouped.has(key))
            grouped.set(key, []);
        grouped.get(key).push(claim);
    }
    // Check each group for contradictions
    for (const [key, groupClaims] of grouped) {
        const contradictions = findContradictionsInGroup(groupClaims, entities);
        if (contradictions.length > 0) {
            const claimIds = [...new Set(contradictions.flatMap((c) => [c.claim_a_id, c.claim_b_id]))];
            contradictionSets.push({
                id: `contradiction_${key}_${Date.now()}`,
                claims: claimIds,
                type: contradictions[0].type,
                context_variance: analyzeContextVariance(contradictions),
                preserved: true,
            });
        }
    }
    return contradictionSets;
}
/**
 * Find pairwise contradictions in a group of claims
 */
function findContradictionsInGroup(claims, entities) {
    const contradictions = [];
    for (let i = 0; i < claims.length; i++) {
        for (let j = i + 1; j < claims.length; j++) {
            const claimA = claims[i];
            const claimB = claims[j];
            const contradiction = checkContradiction(claimA, claimB, entities);
            if (contradiction) {
                contradictions.push(contradiction);
            }
        }
    }
    return contradictions;
}
/**
 * Check if two claims contradict
 */
function checkContradiction(claimA, claimB, entities) {
    // Check temporal overlap - if they don't overlap in time, not a contradiction
    const timeA = getTimeRange(claimA);
    const timeB = getTimeRange(claimB);
    if (!timeRangesOverlap(timeA, timeB))
        return null;
    // Check for direct textual contradiction
    const textContradiction = checkTextualContradiction(claimA, claimB);
    if (textContradiction)
        return textContradiction;
    // Check for context mismatch (same claim, different contexts)
    const contextMismatch = checkContextMismatch(claimA, claimB);
    if (contextMismatch)
        return contextMismatch;
    // Check for source disagreement
    const sourceDisagreement = checkSourceDisagreement(claimA, claimB);
    if (sourceDisagreement)
        return sourceDisagreement;
    // Check for definition/measurement variance
    const measurementVariance = checkMeasurementVariance(claimA, claimB);
    if (measurementVariance)
        return measurementVariance;
    return null;
}
/**
 * Get time range for a claim
 */
function getTimeRange(claim) {
    const start = new Date(claim.valid_from).getTime();
    const end = claim.valid_until ? new Date(claim.valid_until).getTime() : Number.MAX_SAFE_INTEGER;
    return { start, end };
}
/**
 * Check if time ranges overlap
 */
function timeRangesOverlap(rangeA, rangeB) {
    return rangeA.start <= rangeB.end && rangeB.start <= rangeA.end;
}
/**
 * Check for textual contradiction
 */
function checkTextualContradiction(claimA, claimB) {
    const textA = claimA.statement.toLowerCase();
    const textB = claimB.statement.toLowerCase();
    // Direct negation patterns
    const negationPairs = [
        ["independent", "assist"],
        ["independent", "help"],
        ["independent", "supervision"],
        ["walk", "wheelchair"],
        ["walk", "cannot walk"],
        ["able", "unable"],
        ["can", "cannot"],
        ["can", "can't"],
        ["does", "does not"],
        ["is", "is not"],
        ["has", "has not"],
        ["improved", "worse"],
        ["improved", "declined"],
        ["better", "worse"],
        ["increased", "decreased"],
        ["more", "less"],
        ["yes", "no"],
        ["present", "absent"],
        ["normal", "abnormal"],
        ["stable", "unstable"],
    ];
    for (const [pos, neg] of negationPairs) {
        const aHasPos = textA.includes(pos);
        const aHasNeg = textA.includes(neg);
        const bHasPos = textB.includes(pos);
        const bHasNeg = textB.includes(neg);
        if ((aHasPos && bHasNeg) || (aHasNeg && bHasPos)) {
            return createContradiction(claimA, claimB, "source_disagreement", `Textual contradiction: "${pos}" vs "${neg}"`);
        }
    }
    return null;
}
/**
 * Check for context mismatch
 */
function checkContextMismatch(claimA, claimB) {
    const contextDims = ["location", "activity", "time_of_day", "caregiver_present", "assistive_device"];
    for (const dim of contextDims) {
        const contextA = claimA.context[dim];
        const contextB = claimB.context[dim];
        if (contextA && contextB && contextA !== contextB) {
            // Check if statements are similar but contexts differ
            const similarity = calculateTextSimilarity(claimA.statement, claimB.statement);
            if (similarity > 0.6) {
                return createContradiction(claimA, claimB, "context_mismatch", `Same claim in different contexts: ${dim}=${contextA} vs ${dim}=${contextB}`);
            }
        }
    }
    return null;
}
/**
 * Check for source disagreement
 */
function checkSourceDisagreement(claimA, claimB) {
    // Different sources saying different things about same thing
    if (claimA.source_event_ids[0] !== claimB.source_event_ids[0]) {
        const similarity = calculateTextSimilarity(claimA.statement, claimB.statement);
        if (similarity > 0.4 && similarity < 0.9) {
            // Similar topic, different take
            return createContradiction(claimA, claimB, "source_disagreement", `Different sources report different observations`);
        }
    }
    return null;
}
/**
 * Check for measurement variance
 */
function checkMeasurementVariance(claimA, claimB) {
    // Extract numbers and units
    const numsA = extractMeasurements(claimA.statement);
    const numsB = extractMeasurements(claimB.statement);
    if (numsA.length > 0 && numsB.length > 0) {
        // Same type of measurement, different values
        for (const mA of numsA) {
            for (const mB of numsB) {
                if (mA.unit === mB.unit && mA.value !== mB.value) {
                    const diff = Math.abs(mA.value - mB.value) / Math.max(mA.value, mB.value);
                    if (diff > 0.2) { // >20% difference
                        return createContradiction(claimA, claimB, "measurement_variance", `Measurement variance: ${mA.value} ${mA.unit} vs ${mB.value} ${mB.unit}`);
                    }
                }
            }
        }
    }
    return null;
}
/**
 * Extract measurements from text
 */
function extractMeasurements(text) {
    const measurements = [];
    const patterns = [
        /(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|l|kg|lb|cm|mm|in|ft|deg|f|c|bpm|mmhg)/gi,
        /(\d+(?:\.\d+)?)\s*(times?|daily|weekly|monthly)/gi,
        /(\d+(?:\.\d+)?)\s*(hours?|mins?|minutes?|days?|weeks?)/gi,
    ];
    for (const pattern of patterns) {
        const matches = text.matchAll(pattern);
        for (const match of matches) {
            measurements.push({ value: parseFloat(match[1]), unit: match[2].toLowerCase() });
        }
    }
    return measurements;
}
/**
 * Calculate text similarity (simple Jaccard)
 */
function calculateTextSimilarity(textA, textB) {
    const wordsA = new Set(textA.toLowerCase().split(/\W+/).filter((w) => w.length > 2));
    const wordsB = new Set(textB.toLowerCase().split(/\W+/).filter((w) => w.length > 2));
    const intersection = new Set([...wordsA].filter((w) => wordsB.has(w)));
    const union = new Set([...wordsA, ...wordsB]);
    return intersection.size / union.size;
}
/**
 * Create contradiction record
 */
function createContradiction(claimA, claimB, type, resolution_note) {
    return {
        id: `contradiction_${claimA.id}_${claimB.id}_${Date.now()}`,
        type,
        claim_a_id: claimA.id,
        claim_b_id: claimB.id,
        claim_a_summary: claimA.statement.slice(0, 200),
        claim_b_summary: claimB.statement.slice(0, 200),
        context_a: claimA.context,
        context_b: claimB.context,
        resolution_attempted: false,
        resolution_note,
        preserved: true,
    };
}
/**
 * Analyze context variance across contradictions
 */
function analyzeContextVariance(contradictions) {
    const variance = {
        location: [],
        activity: [],
        time_of_day: [],
        caregiver_present: [],
        assistive_device: [],
        social_setting: [],
    };
    for (const c of contradictions) {
        for (const dim of contract_constants_1.CONTEXT_DIMENSIONS) {
            const valA = c.context_a[dim];
            const valB = c.context_b[dim];
            if (valA && !variance[dim].includes(valA))
                variance[dim].push(valA);
            if (valB && !variance[dim].includes(valB))
                variance[dim].push(valB);
        }
    }
    return variance;
}
/**
 * Attempt to resolve contradiction (returns resolution if successful)
 */
function attemptContradictionResolution(contradiction, allClaims) {
    const claimA = allClaims.find((c) => c.id === contradiction.claim_a_id);
    const claimB = allClaims.find((c) => c.id === contradiction.claim_b_id);
    if (!claimA || !claimB)
        return null;
    // Strategy 1: Higher evidence weight wins
    const weightOrder = {
        clinical_assessment: 5,
        caregiver_observation: 4,
        patient_self_report: 3,
        device_data: 4,
        historical_record: 2,
        indirect_inference: 1,
    };
    const weightA = weightOrder[claimA.evidence_weight] || 0;
    const weightB = weightOrder[claimB.evidence_weight] || 0;
    if (weightA > weightB + 1) {
        return { resolved: true, resolution: "higher_evidence_weight", winning_claim_id: claimA.id };
    }
    if (weightB > weightA + 1) {
        return { resolved: true, resolution: "higher_evidence_weight", winning_claim_id: claimB.id };
    }
    // Strategy 2: More recent wins (if significant time gap)
    const timeA = new Date(claimA.valid_from).getTime();
    const timeB = new Date(claimB.valid_from).getTime();
    const timeDiff = Math.abs(timeA - timeB) / (1000 * 60 * 60 * 24); // days
    if (timeDiff > 30) {
        return timeA > timeB
            ? { resolved: true, resolution: "more_recent", winning_claim_id: claimA.id }
            : { resolved: true, resolution: "more_recent", winning_claim_id: claimB.id };
    }
    // Strategy 3: Lower uncertainty wins
    const uncOrder = { none: 0, low: 1, medium: 2, high: 3, unknown: 4 };
    const uncA = uncOrder[claimA.uncertainty];
    const uncB = uncOrder[claimB.uncertainty];
    if (uncA < uncB) {
        return { resolved: true, resolution: "lower_uncertainty", winning_claim_id: claimA.id };
    }
    if (uncB < uncA) {
        return { resolved: true, resolution: "lower_uncertainty", winning_claim_id: claimB.id };
    }
    // Cannot resolve - preserve contradiction
    return { resolved: false, resolution: "preserved_as_contradiction" };
}
/**
 * Format contradiction for caregiver-facing output
 */
function formatContradictionForOutput(contradiction) {
    const contexts = [];
    for (const dim of contract_constants_1.CONTEXT_DIMENSIONS) {
        const valA = contradiction.context_a[dim];
        const valB = contradiction.context_b[dim];
        if (valA || valB) {
            contexts.push(`${dim}: ${valA || "unspecified"} vs ${valB || "unspecified"}`);
        }
    }
    return `Contradiction (${contradiction.type}): "${contradiction.claim_a_summary}" vs "${contradiction.claim_b_summary}". Context: ${contexts.join("; ")}`;
}
