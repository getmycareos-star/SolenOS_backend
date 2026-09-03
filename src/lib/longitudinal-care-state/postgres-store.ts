import type { Pool } from "pg";
import type {
  CareStateAssertion,
  CareStateBaseline,
  CareStateTransition,
  CareStateConflict,
  CareStateDelta,
  CareStateSnapshot,
  StateReconstructionRequest,
  StateReconstructionResult,
  LongitudinalCareState,
  CareStateDimension,
  TransitionMechanism,
} from "./types";
import { CARE_STATE_DIMENSIONS, CARE_STATE_STATUSES, TRANSITION_MECHANISMS } from "./types";

export interface LongitudinalCareStateStore {
  // Assertions
  addAssertion(assertion: CareStateAssertion): Promise<CareStateAssertion>;
  getAssertion(id: string): Promise<CareStateAssertion | null>;
  getAssertionsForDimension(
    care_recipient_id: string,
    dimension: CareStateDimension,
  ): Promise<CareStateAssertion[]>;
  getCurrentAssertions(care_recipient_id: string): Promise<CareStateAssertion[]>;
  getAssertionsAtTime(
    care_recipient_id: string,
    as_of_time: string,
  ): Promise<CareStateAssertion[]>;
  expireAssertion(id: string, validity_end: string): Promise<void>;
  supersedeAssertion(old_id: string, new_assertion: CareStateAssertion): Promise<CareStateAssertion>;

  // Baselines
  establishBaseline(baseline: CareStateBaseline): Promise<CareStateBaseline>;
  getBaselinesForDimension(
    care_recipient_id: string,
    dimension: string,
  ): Promise<CareStateBaseline[]>;
  getBaselineAtTime(
    care_recipient_id: string,
    dimension: string,
    as_of_time: string,
  ): Promise<CareStateBaseline | null>;

  // Transitions
  recordTransition(transition: CareStateTransition): Promise<CareStateTransition>;
  getTransitionsInWindow(
    care_recipient_id: string,
    from: string,
    to: string,
  ): Promise<CareStateTransition[]>;

  // Conflicts
  detectConflicts(care_recipient_id: string): Promise<CareStateConflict[]>;
  resolveConflict(conflict_id: string, resolution: unknown): Promise<void>;

  // Deltas
  computeDelta(
    care_recipient_id: string,
    from_time: string,
    to_time: string,
  ): Promise<CareStateDelta>;

  // Snapshots
  createSnapshot(snapshot: CareStateSnapshot): Promise<CareStateSnapshot>;
  getSnapshotAtTime(
    care_recipient_id: string,
    as_of_time: string,
  ): Promise<CareStateSnapshot | null>;

  // Reconstruction
  reconstructState(request: StateReconstructionRequest): Promise<StateReconstructionResult>;

  // Integrity
  verifyIntegrity(care_recipient_id: string): Promise<string[]>;

  // Migration
  backfillFromStateSituations(): Promise<{ migrated: number; skipped: number }>;
}

export class PostgresLongitudinalCareStateStore implements LongitudinalCareStateStore {
  constructor(private readonly pool: Pool) {}

  // ------------------------------------------------------------------
  // ASSERTIONS
  // ------------------------------------------------------------------

  async addAssertion(assertion: CareStateAssertion): Promise<CareStateAssertion> {
    await this.pool.query(
      `INSERT INTO care_state_assertions (
        id, care_recipient_id, caregiver_id, dimension, value, status,
        validity_start, validity_end, confidence, evidence_ids, event_ids,
        baseline_id, supersedes_id, superseded_by_id, conflict_status,
        provenance_note, created_at, updated_at
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
      ON CONFLICT (id) DO UPDATE SET
        value = EXCLUDED.value,
        status = EXCLUDED.status,
        validity_end = EXCLUDED.validity_end,
        confidence = EXCLUDED.confidence,
        updated_at = EXCLUDED.updated_at`,
      [
        assertion.id,
        assertion.care_recipient_id,
        assertion.caregiver_id,
        assertion.dimension,
        assertion.value,
        assertion.status,
        assertion.validity_start,
        assertion.validity_end,
        assertion.confidence,
        assertion.evidence_ids,
        assertion.event_ids,
        assertion.baseline_id,
        assertion.supersedes_id,
        assertion.superseded_by_id,
        assertion.conflict_status,
        assertion.provenance_note,
        assertion.created_at,
        assertion.updated_at,
      ],
    );
    return assertion;
  }

  async getAssertion(id: string): Promise<CareStateAssertion | null> {
    const result = await this.pool.query(
      `SELECT * FROM care_state_assertions WHERE id = $1`,
      [id],
    );
    return result.rows[0] ?? null;
  }

  async getAssertionsForDimension(
    care_recipient_id: string,
    dimension: CareStateDimension,
  ): Promise<CareStateAssertion[]> {
    const result = await this.pool.query(
      `SELECT * FROM care_state_assertions
       WHERE care_recipient_id = $1 AND dimension = $2
       ORDER BY validity_start ASC`,
      [care_recipient_id, dimension],
    );
    return result.rows;
  }

  async getCurrentAssertions(care_recipient_id: string): Promise<CareStateAssertion[]> {
    const result = await this.pool.query(
      `SELECT * FROM care_state_assertions
       WHERE care_recipient_id = $1
         AND validity_start <= NOW()
         AND (validity_end IS NULL OR validity_end > NOW())
       ORDER BY dimension, validity_start ASC`,
      [care_recipient_id],
    );
    return result.rows;
  }

  async getAssertionsAtTime(
    care_recipient_id: string,
    as_of_time: string,
  ): Promise<CareStateAssertion[]> {
    const result = await this.pool.query(
      `SELECT * FROM care_state_assertions
       WHERE care_recipient_id = $1
         AND validity_start <= $2
         AND (validity_end IS NULL OR validity_end > $2)
       ORDER BY dimension, validity_start ASC`,
      [care_recipient_id, as_of_time],
    );
    return result.rows;
  }

  async expireAssertion(id: string, validity_end: string): Promise<void> {
    await this.pool.query(
      `UPDATE care_state_assertions
       SET validity_end = $2, updated_at = NOW()
       WHERE id = $1 AND validity_end IS NULL`,
      [id, validity_end],
    );
  }

  async supersedeAssertion(
    old_id: string,
    new_assertion: CareStateAssertion,
  ): Promise<CareStateAssertion> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      await client.query(
        `UPDATE care_state_assertions
         SET validity_end = $2, superseded_by_id = $3, updated_at = NOW()
         WHERE id = $1`,
        [old_id, new_assertion.validity_start, new_assertion.id],
      );
      const result = await client.query(
        `INSERT INTO care_state_assertions (
          id, care_recipient_id, caregiver_id, dimension, value, status,
          validity_start, validity_end, confidence, evidence_ids, event_ids,
          baseline_id, supersedes_id, conflict_status, provenance_note,
          created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
        RETURNING *`,
        [
          new_assertion.id,
          new_assertion.care_recipient_id,
          new_assertion.caregiver_id,
          new_assertion.dimension,
          new_assertion.value,
          new_assertion.status,
          new_assertion.validity_start,
          new_assertion.validity_end,
          new_assertion.confidence,
          new_assertion.evidence_ids,
          new_assertion.event_ids,
          new_assertion.baseline_id,
          old_id,
          new_assertion.conflict_status,
          new_assertion.provenance_note,
          new_assertion.created_at,
          new_assertion.updated_at,
        ],
      );
      await client.query("COMMIT");
      return result.rows[0];
    } catch (err) {
      await client.query("ROLLBACK");
      throw err;
    } finally {
      client.release();
    }
  }

  // ------------------------------------------------------------------
  // BASELINES
  // ------------------------------------------------------------------

  async establishBaseline(baseline: CareStateBaseline): Promise<CareStateBaseline> {
    await this.pool.query(
      `INSERT INTO care_state_baselines (
        id, care_recipient_id, dimension, care_state_dimension, value,
        established_at, last_confirmed_at, confidence, evidence_ids, event_ids,
        context, reference_event_id, supersedes_baseline_id, created_at
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
      ON CONFLICT (id) DO UPDATE SET
        value = EXCLUDED.value,
        last_confirmed_at = EXCLUDED.last_confirmed_at,
        confidence = EXCLUDED.confidence`,
      [
        baseline.id,
        baseline.care_recipient_id,
        baseline.dimension,
        baseline.care_state_dimension,
        baseline.value,
        baseline.established_at,
        baseline.last_confirmed_at,
        baseline.confidence,
        baseline.evidence_ids,
        baseline.event_ids,
        baseline.context,
        baseline.reference_event_id,
        baseline.supersedes_baseline_id,
        baseline.created_at,
      ],
    );
    return baseline;
  }

  async getBaselinesForDimension(
    care_recipient_id: string,
    dimension: string,
  ): Promise<CareStateBaseline[]> {
    const result = await this.pool.query(
      `SELECT * FROM care_state_baselines
       WHERE care_recipient_id = $1 AND care_state_dimension = $2
       ORDER BY established_at ASC`,
      [care_recipient_id, dimension],
    );
    return result.rows;
  }

  async getBaselineAtTime(
    care_recipient_id: string,
    dimension: string,
    as_of_time: string,
  ): Promise<CareStateBaseline | null> {
    const result = await this.pool.query(
      `SELECT * FROM care_state_baselines
       WHERE care_recipient_id = $1
         AND care_state_dimension = $2
         AND established_at <= $3
       ORDER BY established_at DESC
       LIMIT 1`,
      [care_recipient_id, dimension, as_of_time],
    );
    return result.rows[0] ?? null;
  }

  // ------------------------------------------------------------------
  // TRANSITIONS
  // ------------------------------------------------------------------

  async recordTransition(transition: CareStateTransition): Promise<CareStateTransition> {
    await this.pool.query(
      `INSERT INTO care_state_transitions (
        id, care_recipient_id, occurred_at, from_assertion_ids, to_assertion_ids,
        mechanism, confidence, evidence_ids, event_ids, detection_method,
        description, created_at
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
      ON CONFLICT (id) DO NOTHING`,
      [
        transition.id,
        transition.care_recipient_id,
        transition.occurred_at,
        transition.from_assertion_ids,
        transition.to_assertion_ids,
        transition.mechanism,
        transition.confidence,
        transition.evidence_ids,
        transition.event_ids,
        transition.detection_method,
        transition.description,
        transition.created_at,
      ],
    );
    return transition;
  }

  async getTransitionsInWindow(
    care_recipient_id: string,
    from: string,
    to: string,
  ): Promise<CareStateTransition[]> {
    const result = await this.pool.query(
      `SELECT * FROM care_state_transitions
       WHERE care_recipient_id = $1
         AND occurred_at >= $2
         AND occurred_at <= $3
       ORDER BY occurred_at ASC`,
      [care_recipient_id, from, to],
    );
    return result.rows;
  }

  // ------------------------------------------------------------------
  // CONFLICTS
  // ------------------------------------------------------------------

  async detectConflicts(care_recipient_id: string): Promise<CareStateConflict[]> {
    const result = await this.pool.query(
      `SELECT * FROM care_state_conflicts
       WHERE care_recipient_id = $1 AND resolved = false`,
      [care_recipient_id],
    );
    return result.rows;
  }

  async resolveConflict(conflict_id: string, resolution: unknown): Promise<void> {
    await this.pool.query(
      `UPDATE care_state_conflicts
       SET resolved = true, resolution = $2
       WHERE id = $1`,
      [conflict_id, JSON.stringify(resolution)],
    );
  }

  // ------------------------------------------------------------------
  // DELTAS
  // ------------------------------------------------------------------

  async computeDelta(
    care_recipient_id: string,
    from_time: string,
    to_time: string,
  ): Promise<CareStateDelta> {
    const fromAssertions = await this.getAssertionsAtTime(care_recipient_id, from_time);
    const toAssertions = await this.getAssertionsAtTime(care_recipient_id, to_time);

    const fromMap = new Map(fromAssertions.map((a) => [a.dimension, a]));
    const toMap = new Map(toAssertions.map((a) => [a.dimension, a]));

    const additions: CareStateAssertion[] = [];
    const removals: CareStateAssertion[] = [];
    const modifications: CareStateDelta["modifications"] = [];

    for (const [dim, toA] of toMap) {
      const fromA = fromMap.get(dim);
      if (!fromA) {
        additions.push(toA);
      } else if (fromA.value !== toA.value || fromA.status !== toA.status) {
        modifications.push({
          assertion_id: toA.id,
          dimension: dim,
          from_value: fromA.value,
          to_value: toA.value,
        });
      }
    }

    for (const [dim, fromA] of fromMap) {
      if (!toMap.has(dim)) {
        removals.push(fromA);
      }
    }

    const learning_type =
      additions.some((a) => a.supersedes_id) || removals.some((a) => a.superseded_by_id)
        ? ("retroactive_correction" as const)
        : ("new_observation" as const);

    const delta: CareStateDelta = {
      id: `delta-${care_recipient_id}-${from_time}-${to_time}`,
      care_recipient_id,
      computed_at: new Date().toISOString(),
      from_time,
      to_time,
      additions,
      removals,
      modifications,
      learning_type,
      description: `Delta from ${from_time} to ${to_time}: ${additions.length} additions, ${removals.length} removals, ${modifications.length} modifications`,
    };

    await this.pool.query(
      `INSERT INTO care_state_deltas (
        id, care_recipient_id, from_time, to_time, additions, removals,
        modifications, learning_type, description
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
      ON CONFLICT (id) DO UPDATE SET
        computed_at = NOW(), additions = EXCLUDED.additions, removals = EXCLUDED.removals`,
      [
        delta.id,
        delta.care_recipient_id,
        delta.from_time,
        delta.to_time,
        JSON.stringify(delta.additions),
        JSON.stringify(delta.removals),
        JSON.stringify(delta.modifications),
        delta.learning_type,
        delta.description,
      ],
    );

    return delta;
  }

  // ------------------------------------------------------------------
  // SNAPSHOTS
  // ------------------------------------------------------------------

  async createSnapshot(snapshot: CareStateSnapshot): Promise<CareStateSnapshot> {
    await this.pool.query(
      `INSERT INTO care_state_snapshots (
        id, care_recipient_id, as_of_time, assertions, baselines,
        transition_count, confidence_summary, materialized
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
      ON CONFLICT (id) DO UPDATE SET
        assertions = EXCLUDED.assertions,
        baselines = EXCLUDED.baselines,
        transition_count = EXCLUDED.transition_count,
        confidence_summary = EXCLUDED.confidence_summary,
        computed_at = NOW()`,
      [
        snapshot.id,
        snapshot.care_recipient_id,
        snapshot.as_of_time,
        JSON.stringify(snapshot.assertions),
        JSON.stringify(snapshot.baselines),
        snapshot.transition_count,
        snapshot.confidence_summary,
        snapshot.materialized,
      ],
    );
    return snapshot;
  }

  async getSnapshotAtTime(
    care_recipient_id: string,
    as_of_time: string,
  ): Promise<CareStateSnapshot | null> {
    const result = await this.pool.query(
      `SELECT * FROM care_state_snapshots
       WHERE care_recipient_id = $1 AND as_of_time = $2
       LIMIT 1`,
      [care_recipient_id, as_of_time],
    );
    return result.rows[0] ?? null;
  }

  // ------------------------------------------------------------------
  // RECONSTRUCTION
  // ------------------------------------------------------------------

  async reconstructState(request: StateReconstructionRequest): Promise<StateReconstructionResult> {
    const assertions = await this.getAssertionsAtTime(
      request.care_recipient_id,
      request.as_of_time,
    );
    const conflicts = await this.detectConflicts(request.care_recipient_id);
    const transitions = await this.getTransitionsInWindow(
      request.care_recipient_id,
      new Date(new Date(request.as_of_time).getTime() - 30 * 24 * 60 * 60 * 1000).toISOString(),
      request.as_of_time,
    );

    const unknownDimensions = CARE_STATE_DIMENSIONS.filter((d) => {
      return !assertions.some((a) => a.dimension === d);
    });

    const confidence =
      assertions.length > 0
        ? assertions.reduce((sum, a) => sum + Number(a.confidence), 0) / assertions.length
        : 0;

    const snapshot: CareStateSnapshot = {
      id: `snapshot-${request.care_recipient_id}-${request.as_of_time}`,
      care_recipient_id: request.care_recipient_id,
      computed_at: new Date().toISOString(),
      as_of_time: request.as_of_time,
      assertions,
      baselines: [],
      transition_count: transitions.length,
      confidence_summary: confidence,
      materialized: false,
    };

    return {
      care_recipient_id: request.care_recipient_id,
      as_of_time: request.as_of_time,
      state: snapshot,
      unknown_dimensions: unknownDimensions,
      conflicts,
      recent_transitions: transitions,
      gaps: [],
      confidence,
    };
  }

  // ------------------------------------------------------------------
  // INTEGRITY
  // ------------------------------------------------------------------

  async verifyIntegrity(care_recipient_id: string): Promise<string[]> {
    const violations: string[] = [];

    const assertions = await this.getCurrentAssertions(care_recipient_id);
    for (const a of assertions) {
      if (!CARE_STATE_DIMENSIONS.includes(a.dimension)) {
        violations.push(`Invalid dimension: ${a.dimension}`);
      }
      if (!CARE_STATE_STATUSES.includes(a.status)) {
        violations.push(`Invalid status: ${a.status}`);
      }
      if (a.confidence < 0 || a.confidence > 1) {
        violations.push(`Invalid confidence: ${a.confidence}`);
      }
    }

    const conflicts = await this.detectConflicts(care_recipient_id);
    if (conflicts.length > 0) {
      violations.push(`Unresolved conflicts: ${conflicts.length}`);
    }

    return violations;
  }

  // ------------------------------------------------------------------
  // MIGRATION
  // ------------------------------------------------------------------

  async backfillFromStateSituations(): Promise<{ migrated: number; skipped: number }> {
    const result = await this.pool.query(
      `SELECT id, careSessionId, userId, status, summary, createdAt, updatedAt
       FROM state_situations
       WHERE NOT EXISTS (
        SELECT 1 FROM care_state_assertions
        WHERE care_state_assertions.evidence_ids @> ARRAY[state_situations.id]
      )`,
    );

    let migrated = 0;
    let skipped = 0;

    for (const row of result.rows) {
      try {
        const assertion: CareStateAssertion = {
          id: `assertion-legacy-${row.id}`,
          care_recipient_id: row.careSessionId,
          caregiver_id: row.userId,
          dimension: "active_conditions",
          value: row.summary || "Legacy situation",
          status: row.status === "resolved" ? "resolved" : "active",
          validity_start: row.createdAt,
          validity_end: row.status === "resolved" ? row.updatedAt : null,
          confidence: 0.5,
          evidence_ids: [row.id],
          event_ids: [],
          conflict_status: "coexisting",
          provenance_note: "Backfilled from legacy StateSituation",
          created_at: row.createdAt,
          updated_at: row.updatedAt,
        };
        await this.addAssertion(assertion);
        migrated++;
      } catch {
        skipped++;
      }
    }

    return { migrated, skipped };
  }
}
