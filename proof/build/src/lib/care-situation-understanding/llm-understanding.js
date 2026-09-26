"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.llmStructuredUnderstanding = llmStructuredUnderstanding;
exports.deterministicUnderstanding = deterministicUnderstanding;
const extract_1 = require("../care-reality-extraction/extract");
const llm_prompt_1 = require("./llm-prompt");
const llm_schema_1 = require("./llm-schema");
const llm_1 = require("../llm");
/**
 * Map LLM typed output to existing CareRealityExtractionResult types.
 * Assigns stable IDs and maintains structure compatibility.
 */
function mapLlmOutputToExtractionResult(llm) {
    const observations = llm.observations.map((o, i) => ({
        id: `llm_obs_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
        layer: "observation",
        description: o.description,
        approximate_time: o.approximate_time,
        source: "caregiver",
        confidence: o.confidence,
        raw_fragment: o.raw_fragment,
    }));
    const events = llm.events.map((e, i) => ({
        id: `llm_evt_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
        layer: "event",
        description: e.description,
        time: e.time,
        participants: e.participants,
        related_observation_ids: [],
        raw_fragment: e.raw_fragment,
    }));
    const decisions = llm.decisions.map((d, i) => ({
        id: `llm_dec_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
        layer: "decision",
        description: d.description,
        who: d.who,
        why: d.why,
        reason_unknown: d.reason_unknown,
        evidence_texts: [],
        alternatives: [],
        outcome: null,
        status: d.status,
        raw_fragment: d.raw_fragment,
    }));
    const outcomes = llm.outcomes.map((o, i) => ({
        id: `llm_out_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
        layer: "outcome",
        description: o.description,
        related_id: null,
        related_type: null,
        time: null,
        evidence_texts: [],
        status: o.status,
        raw_fragment: o.raw_fragment,
    }));
    const unknowns = llm.unknowns.map((u, i) => ({
        id: `llm_unk_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
        layer: "unknown",
        question: u.question,
        related_object_id: null,
        related_object_type: null,
        source: "caregiver",
        importance: "Identified by structured understanding layer — needs confirmation.",
        status: u.status,
        raw_fragment: u.raw_fragment,
    }));
    const non_care_facts = llm.non_care_facts.map((n, i) => ({
        id: `llm_ncf_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
        layer: n.layer,
        text: n.text,
        raw_fragment: n.raw_fragment,
    }));
    const relationships = llm.possible_links.map((l, i) => ({
        id: `llm_rel_${Date.now().toString(36)}_${i}_${Math.random().toString(36).slice(2, 7)}`,
        from_id: "",
        to_id: "",
        kind: "observation_to_observation",
        certainty: "possible",
        evidence_note: l.text,
    }));
    return {
        observations,
        events,
        decisions,
        actions: [],
        outcomes,
        unknowns,
        non_care_facts,
        relationships,
        observation_focus_lines: observations.map((o) => o.description.endsWith(".") ? o.description : `${o.description}.`),
    };
}
/**
 * Attempt to extract care reality using the local LLM (Ollama / qwen3-coder:30b) for structured understanding.
 * Falls back to deterministic/regex extraction on any failure.
 *
 * @param rawText - The caregiver's raw input text (any length, any structure)
 * @param contributorId - Optional contributor id for attribution
 * @returns CareRealityExtractionResult from LLM (if successful) or deterministic fallback
 */
async function llmStructuredUnderstanding(params) {
    const { rawText } = params;
    const source = params.contributorId ?? "caregiver";
    // Trim excessively long input — LLM context preserved but extraction bounded
    const trimmedText = rawText.trim();
    if (!trimmedText) {
        return (0, extract_1.extractCareRealityFromText)({ rawText: "", source });
    }
    // Gate: only attempt local-LLM extraction when Ollama is reachable.
    // If it is not available, fall back to deterministic extraction (no external
    // dependency, no blocked input — raw caregiver text is always preserved).
    const provider = (0, llm_1.getLlmProvider)();
    if (!(await provider.isAvailable(params.signal))) {
        return (0, extract_1.extractCareRealityFromText)({ rawText: trimmedText, source });
    }
    try {
        const response = await provider.invoke({
            system: llm_prompt_1.CARE_UNDERSTANDING_LLM_SYSTEM_PROMPT,
            user: trimmedText,
            temperature: 0,
            json: true,
            signal: params.signal,
        });
        const content = response.content;
        // Extract JSON from response (handle markdown-wrapped JSON)
        const jsonMatch = content.match(/\{[\s\S]*\}/);
        if (!jsonMatch) {
            return (0, extract_1.extractCareRealityFromText)({ rawText: trimmedText, source });
        }
        const parsed = JSON.parse(jsonMatch[0]);
        // Validate against Zod schema
        const validation = llm_schema_1.LlmUnderstandingOutputSchema.safeParse(parsed);
        if (!validation.success) {
            console.warn("[llm-understanding] Zod validation failed:", validation.error.issues.map((i) => i.message).join("; "));
            return (0, extract_1.extractCareRealityFromText)({ rawText: trimmedText, source });
        }
        const validated = validation.data;
        // Medical boundary check — forbid diagnosis/advice/empathy/causation
        const boundary = (0, llm_schema_1.validateMedicalBoundary)(validated);
        if (!boundary.ok) {
            console.warn("[llm-understanding] Medical boundary violation:", boundary.failures.join("; "));
            return (0, extract_1.extractCareRealityFromText)({ rawText: trimmedText, source });
        }
        // Map to existing types and return
        return mapLlmOutputToExtractionResult(validated);
    }
    catch (err) {
        console.warn("[llm-understanding] LLM extraction failed, falling back to deterministic:", err instanceof Error ? err.message : String(err));
        return (0, extract_1.extractCareRealityFromText)({ rawText: trimmedText, source });
    }
}
/**
 * Synchronous deterministic-only understanding path.
 * Always uses regex/heuristic extraction — no LLM dependency.
 * Use when you need guaranteed synchronous execution.
 */
function deterministicUnderstanding(params) {
    return (0, extract_1.extractCareRealityFromText)({
        rawText: params.rawText,
        source: params.contributorId ?? "caregiver",
    });
}
