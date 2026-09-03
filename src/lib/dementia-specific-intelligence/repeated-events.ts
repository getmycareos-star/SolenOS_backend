/**
 * Repeated-event detection and pattern aggregation.
 *
 * Hard rules:
 *   - Repeated documentation ≠ repeated events. De-dup by (source_id,
 *     observation_time, content_fingerprint). Two notes that are
 *     near-identical text authored within the same window by the same
 *     source collapse to ONE event.
 *   - A pattern requires ≥N *independent* observations within a bounded
 *     window. N is parameterized; no hardcoded count.
 *   - Single event ≠ pattern. NEVER.
 *   - Pattern confidence is independent of observation confidence.
 *   - Acute vs recurring vs chronic is preserved as a temporal class,
 *     never interpreted as etiology.
 */

import type {
  CognitiveObservation,
  ConfusionObservation,
  BehavioralObservation,
  FunctionalObservation,
  SafetyObservation,
  Pattern,
  ObservationDomain,
  SourceType,
} from "./types";

// ─── Configuration ────────────────────────────────────────────────────────

export type PatternConfig = {
  /** Minimum independent observations for a pattern (default 3) */
  min_independent_count: number;
  /** Window in days within which observations count toward a pattern */
  window_days: number;
  /** Minimum distinct source count to claim a cross-source pattern */
  min_distinct_sources: number;
  /** Whether acute-only events are eligible for patterns (default false) */
  include_acute_only: boolean;
};

export const DEFAULT_PATTERN_CONFIG: PatternConfig = {
  min_independent_count: 3,
  window_days: 21,
  min_distinct_sources: 1,
  include_acute_only: false,
};

// ─── De-duplication ───────────────────────────────────────────────────────

/**
 * A fingerprint for de-duplication. Two observations with the same
 * `(source_id, observation_time-bucketed, content_signature)` collapse
 * to a single event.
 */
export type DedupeKey = {
  source_id: string;
  time_bucket: string;
  content_signature: string;
};

export function makeDedupeKey(params: {
  source_id: string;
  observation_time: string | null;
  content: string;
  bucket_minutes?: number;
}): DedupeKey {
  const bucket = bucketTime(params.observation_time, params.bucket_minutes ?? 60);
  return {
    source_id: params.source_id,
    time_bucket: bucket,
    content_signature: normalizeForFingerprint(params.content),
  };
}

function bucketTime(iso: string | null, minutes: number): string {
  if (!iso) return "unknown";
  const t = new Date(iso);
  if (isNaN(t.getTime())) return "unknown";
  const ms = t.getTime();
  const bucketMs = minutes * 60 * 1000;
  return String(Math.floor(ms / bucketMs) * bucketMs);
}

function normalizeForFingerprint(s: string): string {
  return s
    .toLowerCase()
    .replace(/\b(she|he|they|the|a|an)\b/g, "")
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
}

/**
 * De-duplicate a list of observations by `(source_id, time_bucket,
 * content_signature)`. The first occurrence is preserved.
 */
export function dedupeObservations<T extends { observation_id: string; observation_time: string | null; provenance: { source_type: SourceType; observer_id: string | null; raw_text: string } }>(
  observations: readonly T[],
  bucket_minutes?: number,
): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const obs of observations) {
    const key = makeDedupeKey({
      source_id: obs.provenance.observer_id ?? obs.provenance.source_type,
      observation_time: obs.observation_time,
      content: obs.provenance.raw_text,
      bucket_minutes,
    });
    const k = `${key.source_id}|${key.time_bucket}|${key.content_signature}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(obs);
  }
  return out;
}

// ─── Pattern detection ────────────────────────────────────────────────────

export type PatternCandidate = {
  observation_ids: string[];
  source_ids: Set<string>;
  window_start: string;
  window_end: string;
  temporal_class: Pattern["temporal_class"];
  pattern_kind: string;
  domain: ObservationDomain;
};

/**
 * Build a pattern from a list of observations. Returns `null` if the
 * list does not meet the configured threshold.
 */
export function buildPatternFromObservations(params: {
  pattern_id: string;
  subject_id: string;
  pattern_kind: string;
  domain: ObservationDomain;
  observations: ReadonlyArray<{
    observation_id: string;
    observation_time: string | null;
    source_id: string;
  }>;
  config?: PatternConfig;
}): Pattern | null {
  const cfg = params.config ?? DEFAULT_PATTERN_CONFIG;
  const sorted = [...params.observations].sort((a, b) => sortByTime(a.observation_time, b.observation_time));
  if (sorted.length < cfg.min_independent_count) return null;
  const distinctSources = new Set(sorted.map((o) => o.source_id));
  if (distinctSources.size < cfg.min_distinct_sources) return null;
  const windowStart = sorted[0].observation_time;
  const windowEnd = sorted[sorted.length - 1].observation_time;
  if (windowStart && windowEnd) {
    const days = (new Date(windowEnd).getTime() - new Date(windowStart).getTime()) / (1000 * 60 * 60 * 24);
    if (days > cfg.window_days) return null;
  }
  const temporal = classifyTemporalClass(sorted);
  if (!cfg.include_acute_only && temporal === "acute") return null;

  return {
    pattern_id: params.pattern_id,
    subject_id: params.subject_id,
    domain: params.domain,
    pattern_kind: params.pattern_kind,
    component_observation_ids: sorted.map((o) => o.observation_id),
    window_start: windowStart,
    window_end: windowEnd,
    independent_observation_count: sorted.length,
    pattern_confidence: distinctSources.size >= 2 ? "high" : "medium",
    temporal_class: temporal,
    distinct_source_count: distinctSources.size,
    direction: "unknown",
  };
}

function sortByTime(a: string | null, b: string | null): number {
  if (!a && !b) return 0;
  if (!a) return 1;
  if (!b) return -1;
  return new Date(a).getTime() - new Date(b).getTime();
}

function classifyTemporalClass(
  sorted: ReadonlyArray<{ observation_time: string | null }>,
): Pattern["temporal_class"] {
  if (sorted.length === 0) return "unknown";
  const first = sorted[0].observation_time;
  const last = sorted[sorted.length - 1].observation_time;
  if (!first || !last) return "unknown";
  const days = (new Date(last).getTime() - new Date(first).getTime()) / (1000 * 60 * 60 * 24);
  // Multiple distinct events forming a pattern: classify by span.
  // A 2-day span across 3 events is a recurring pattern, not acute.
  // Acute is reserved for one-off acute events that did NOT form a pattern.
  if (days <= 1) return "recurring";
  if (days <= 30) return "recurring";
  return "chronic";
}

// ─── Repeated-question pattern (specialized) ──────────────────────────────

export type RepeatedQuestionResult = {
  /** A pattern, or null if not enough evidence */
  pattern: Pattern | null;
  /** Independent count after dedup */
  independent_count: number;
  /** Distinct source count */
  distinct_source_count: number;
  /** Total times observed (with duplicates collapsed) */
};

/**
 * Detect a repeated-question pattern from cognitive observations.
 *
 * Specifically:
 *   - Filters to `observation_type === "repeated_question"`.
 *   - De-duplicates by `(source, time bucket, content fingerprint)`.
 *   - Requires ≥ min_independent_count independent observations.
 *   - Pattern is qualified as `care_relevant` only if there is also a
 *     functional or safety consequence (delegated to care-relevance.ts).
 */
export function detectRepeatedQuestionPattern(params: {
  pattern_id: string;
  subject_id: string;
  cognitive_observations: readonly CognitiveObservation[];
  config?: PatternConfig;
}): RepeatedQuestionResult {
  const cfg = params.config ?? DEFAULT_PATTERN_CONFIG;
  const filtered = params.cognitive_observations.filter((o) => o.observation_type === "repeated_question");
  const deduped = dedupeObservations(filtered);
  const obs = deduped.map((o) => ({
    observation_id: o.observation_id,
    observation_time: o.observation_time,
    source_id: o.provenance.observer_id ?? o.provenance.source_type,
  }));
  const pattern = buildPatternFromObservations({
    pattern_id: params.pattern_id,
    subject_id: params.subject_id,
    pattern_kind: "repeated_question",
    domain: "cognition",
    observations: obs,
    config: cfg,
  });
  return {
    pattern,
    independent_count: deduped.length,
    distinct_source_count: new Set(obs.map((o) => o.source_id)).size,
  };
}

// ─── Confusion-episode pattern ────────────────────────────────────────────

export type ConfusionEpisodePatternResult = {
  pattern: Pattern | null;
  independent_count: number;
  /** Acute flag — set true if the most recent episode is acute */
  acute_change_flag: boolean;
};

export function detectConfusionEpisodePattern(params: {
  pattern_id: string;
  subject_id: string;
  confusion_observations: readonly ConfusionObservation[];
  config?: PatternConfig;
}): ConfusionEpisodePatternResult {
  const cfg = params.config ?? DEFAULT_PATTERN_CONFIG;
  const deduped = dedupeObservations(params.confusion_observations);
  const obs = deduped.map((o) => ({
    observation_id: o.observation_id,
    observation_time: o.observation_time,
    source_id: o.provenance.observer_id ?? o.provenance.source_type,
  }));
  const pattern = buildPatternFromObservations({
    pattern_id: params.pattern_id,
    subject_id: params.subject_id,
    pattern_kind: "confusion_episode",
    domain: "cognition",
    observations: obs,
    config: cfg,
  });
  // Acute-change flag: the latest observation is acute
  const latest = [...deduped].sort((a, b) => sortByTime(a.observation_time, b.observation_time)).pop();
  const acute_change_flag = latest?.attributes.onset === "acute" && deduped.length >= 1;
  return {
    pattern,
    independent_count: deduped.length,
    acute_change_flag: Boolean(acute_change_flag),
  };
}

// ─── Behavior / safety / functional pattern helpers ───────────────────────

export function detectBehaviorPattern(params: {
  pattern_id: string;
  subject_id: string;
  behavioral_observations: readonly BehavioralObservation[];
  config?: PatternConfig;
}): Pattern | null {
  const cfg = params.config ?? DEFAULT_PATTERN_CONFIG;
  const deduped = dedupeObservations(params.behavioral_observations);
  const obs = deduped.map((o) => ({
    observation_id: o.observation_id,
    observation_time: o.observation_time,
    source_id: o.provenance.observer_id ?? o.provenance.source_type,
  }));
  return buildPatternFromObservations({
    pattern_id: params.pattern_id,
    subject_id: params.subject_id,
    pattern_kind: "behavioral_observations",
    domain: "behavior",
    observations: obs,
    config: cfg,
  });
}

export function detectSafetyPattern(params: {
  pattern_id: string;
  subject_id: string;
  safety_observations: readonly SafetyObservation[];
  config?: PatternConfig;
}): Pattern | null {
  const cfg = params.config ?? DEFAULT_PATTERN_CONFIG;
  const deduped = dedupeObservations(params.safety_observations);
  const obs = deduped.map((o) => ({
    observation_id: o.observation_id,
    observation_time: o.observation_time,
    source_id: o.provenance.observer_id ?? o.provenance.source_type,
  }));
  return buildPatternFromObservations({
    pattern_id: params.pattern_id,
    subject_id: params.subject_id,
    pattern_kind: "safety_observations",
    domain: "safety",
    observations: obs,
    config: cfg,
  });
}

export function detectFunctionalPattern(params: {
  pattern_id: string;
  subject_id: string;
  functional_observations: readonly FunctionalObservation[];
  config?: PatternConfig;
}): Pattern | null {
  const cfg = params.config ?? DEFAULT_PATTERN_CONFIG;
  const deduped = dedupeObservations(params.functional_observations);
  const obs = deduped.map((o) => ({
    observation_id: o.observation_id,
    observation_time: o.observation_time,
    source_id: o.provenance.observer_id ?? o.provenance.source_type,
  }));
  return buildPatternFromObservations({
    pattern_id: params.pattern_id,
    subject_id: params.subject_id,
    pattern_kind: "functional_observations",
    domain: "function",
    observations: obs,
    config: cfg,
  });
}
