import type { Pool } from "pg";
import type { CanonicalCareEvent, CareContextRoot } from "../situation-entry/types";
import type { CareEpisode, ContinuityLink, EpisodeKind, EpisodeStatus, MemoryLayerStore } from "../care-memory-layers/types";
import type { CareRecordStore } from "../care-record-store/types";
import type { UnresolvedQuestion } from "../care-record-store/unresolved-questions";
import { createPostgresPool } from "../telemetry-persistence/postgres-store";

let poolPromise: Promise<Pool> | null = null;

async function getPool(): Promise<Pool> {
  if (!poolPromise) {
    poolPromise = createPostgresPool();
  }
  return poolPromise;
}

interface CareContextRow {
  id: string;
  care_recipient_id: string;
  caregiver_id: string;
  root_event_id: string | null;
  multi_caregiver: CareContextRoot["multi_caregiver"];
  created_at: string;
  updated_at: string;
}

interface CareEventRow {
  id: string;
  event_data: CanonicalCareEvent;
}

interface EpisodeRow {
  id: string;
  kind: string;
  status: string;
  summary: string | null;
  event_ids: string[];
  opened_at: string;
  closed_at: string | null;
}

interface ContinuityLinkRow {
  id: string;
  from_event_id: string;
  to_event_id: string;
  link_type: string;
  weight: number;
  metadata: Record<string, unknown>;
}

interface UnresolvedQuestionRow {
  id: string;
  care_recipient_id: string;
  question: string;
  source_event_id: string | null;
  status: string;
  resolved_at: string | null;
  resolution_event_id: string | null;
  created_at: string;
  updated_at: string;
}

function mapContextRowToCareContextRoot(
  row: CareContextRow,
  events: CanonicalCareEvent[],
): CareContextRoot {
  return {
    id: "CareContextRoot",
    care_recipient_id: row.care_recipient_id,
    caregiver_id: row.caregiver_id,
    events,
    root_event_id: row.root_event_id,
    created_at: row.created_at,
    updated_at: row.updated_at,
    multi_caregiver: row.multi_caregiver,
  };
}

function mapUnresolvedRowToQuestion(row: UnresolvedQuestionRow): UnresolvedQuestion {
  return {
    id: row.id,
    careRecipientId: row.care_recipient_id,
    question: row.question,
    sourceEventId: row.source_event_id,
    status: row.status as UnresolvedQuestion["status"],
    resolvedAt: row.resolved_at,
    resolutionEventId: row.resolution_event_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export class PostgresCareRecordStore implements CareRecordStore {
  async getCareContextRoot(careRecipientId: string): Promise<CareContextRoot | null> {
    const pool = await getPool();
    const contextResult = await pool.query<CareContextRow>(
      `SELECT id, care_recipient_id, caregiver_id, root_event_id, multi_caregiver, created_at, updated_at
       FROM care_context_root WHERE care_recipient_id = $1`,
      [careRecipientId],
    );
    if (contextResult.rowCount === 0 || !contextResult.rows[0]) {
      return null;
    }
    const contextRow = contextResult.rows[0];

    const eventsResult = await pool.query<CareEventRow>(
      `SELECT id, event_data FROM care_record_events WHERE care_recipient_id = $1 ORDER BY created_at ASC`,
      [careRecipientId],
    );
    const events = eventsResult.rows.map((r) => r.event_data);

    return mapContextRowToCareContextRoot(contextRow, events);
  }

  async appendEvents(careRecipientId: string, events: CanonicalCareEvent[]): Promise<CareContextRoot> {
    const pool = await getPool();
    const client = await pool.connect();
    try {
      await client.query("BEGIN");

      const contextResult = await client.query<CareContextRow>(
        `SELECT id, care_recipient_id, caregiver_id, root_event_id, multi_caregiver, created_at, updated_at
         FROM care_context_root WHERE care_recipient_id = $1 FOR UPDATE`,
        [careRecipientId],
      );

      if (contextResult.rowCount === 0 || !contextResult.rows[0]) {
        await client.query(
          `INSERT INTO care_context_root (id, care_recipient_id, caregiver_id, root_event_id, multi_caregiver, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5::jsonb, NOW(), NOW())`,
          [`CareContextRoot::${careRecipientId}`, careRecipientId, careRecipientId, events[0]?.id ?? null, "{}"],
        );
      } else {
        await client.query(
          `UPDATE care_context_root SET updated_at = NOW() WHERE care_recipient_id = $1`,
          [careRecipientId],
        );
      }

      for (const event of events) {
        await client.query(
          `INSERT INTO care_record_events (id, care_recipient_id, event_data, created_at)
           VALUES ($1, $2, $3::jsonb, NOW())
           ON CONFLICT (id) DO UPDATE SET event_data = $3::jsonb`,
          [event.id, careRecipientId, JSON.stringify(event)],
        );
      }

      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }

    const root = await this.getCareContextRoot(careRecipientId);
    if (!root) {
      throw new Error(`Failed to persist care context for ${careRecipientId}`);
    }
    return root;
  }

  async invalidateEvent(
    careRecipientId: string,
    eventId: string,
    reason?: string,
  ): Promise<CareContextRoot | null> {
    const pool = await getPool();
    const eventResult = await pool.query<CareEventRow>(
      `SELECT event_data FROM care_record_events WHERE id = $1 AND care_recipient_id = $2`,
      [eventId, careRecipientId],
    );
    if (eventResult.rowCount === 0 || !eventResult.rows[0]) {
      return null;
    }
    const event = eventResult.rows[0].event_data;
    const invalidated: CanonicalCareEvent = {
      ...event,
      status: "invalidated",
      attributes: { ...event.attributes, invalidated_reason: reason ?? "invalidated" },
    };
    await pool.query(
      `UPDATE care_record_events SET event_data = $2::jsonb WHERE id = $1`,
      [eventId, JSON.stringify(invalidated)],
    );
    return this.getCareContextRoot(careRecipientId);
  }

  async applyUserCorrection(
    careRecipientId: string,
    eventId: string,
    fields: Record<string, unknown>,
    reason?: string,
  ): Promise<CareContextRoot | null> {
    const pool = await getPool();
    const eventResult = await pool.query<CareEventRow>(
      `SELECT event_data FROM care_record_events WHERE id = $1 AND care_recipient_id = $2`,
      [eventId, careRecipientId],
    );
    if (eventResult.rowCount === 0 || !eventResult.rows[0]) {
      return null;
    }
    const event = eventResult.rows[0].event_data;
    const corrected: CanonicalCareEvent = {
      ...event,
      attributes: { ...event.attributes, ...fields, correction_reason: reason ?? "user_correction" },
    };
    await pool.query(
      `UPDATE care_record_events SET event_data = $2::jsonb WHERE id = $1`,
      [eventId, JSON.stringify(corrected)],
    );
    return this.getCareContextRoot(careRecipientId);
  }

  async supersedeEvent(
    careRecipientId: string,
    originalEventId: string,
    replacement: CanonicalCareEvent,
    reason?: string,
  ): Promise<{ superseded: CanonicalCareEvent; active: CanonicalCareEvent } | null> {
    const pool = await getPool();
    const eventResult = await pool.query<CareEventRow>(
      `SELECT event_data FROM care_record_events WHERE id = $1 AND care_recipient_id = $2`,
      [originalEventId, careRecipientId],
    );
    if (eventResult.rowCount === 0 || !eventResult.rows[0]) {
      return null;
    }
    const original = eventResult.rows[0].event_data;
    const superseded: CanonicalCareEvent = {
      ...original,
      status: "superseded",
      attributes: { ...original.attributes, superseded_by: replacement.id, supersede_reason: reason ?? "superseded" },
    };
    const active: CanonicalCareEvent = {
      ...replacement,
      root_event_id: original.root_event_id,
    };

    const client = await pool.connect();
    try {
      await client.query("BEGIN");
      await client.query(
        `UPDATE care_record_events SET event_data = $2::jsonb WHERE id = $1`,
        [originalEventId, JSON.stringify(superseded)],
      );
      await client.query(
        `INSERT INTO care_record_events (id, care_recipient_id, event_data, created_at)
         VALUES ($1, $2, $3::jsonb, NOW())
         ON CONFLICT (id) DO UPDATE SET event_data = $3::jsonb`,
        [active.id, careRecipientId, JSON.stringify(active)],
      );
      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
    return { superseded, active };
  }

  async getMemoryLayers(careRecipientId: string): Promise<MemoryLayerStore | null> {
    const pool = await getPool();
    const episodesResult = await pool.query<EpisodeRow>(
      `SELECT id, kind, status, summary, event_ids, opened_at, closed_at
       FROM care_episodes WHERE care_recipient_id = $1`,
      [careRecipientId],
    );
    const linksResult = await pool.query<ContinuityLinkRow>(
      `SELECT id, from_event_id, to_event_id, link_type, weight, metadata
       FROM continuity_links WHERE care_recipient_id = $1`,
      [careRecipientId],
    );

    const episodes: CareEpisode[] = episodesResult.rows.map((r) => ({
      id: r.id,
      layer: "episode",
      caregiver_id: careRecipientId,
      title: r.summary ?? r.kind,
      kind: r.kind as EpisodeKind,
      status: r.status as EpisodeStatus,
      event_ids: r.event_ids,
      started_at: r.opened_at,
      ended_at: r.closed_at,
      summary: r.summary ?? "",
      source_event_ids: r.event_ids,
      created_at: r.opened_at,
      updated_at: r.closed_at ?? r.opened_at,
    }));

    const links: ContinuityLink[] = linksResult.rows.map((r) => ({
      id: r.id,
      from_event_id: r.from_event_id,
      to_event_id: r.to_event_id,
      link_type: r.link_type as ContinuityLink["link_type"],
      note: "",
      created_at: new Date().toISOString(),
    }));

    const activeEpisode = episodes.find((e) => e.status === "active") ?? null;

    return {
      caregiver_id: careRecipientId,
      raw_event_refs: [],
      structured: {
        layer: "structured_continuity",
        caregiver_id: careRecipientId,
        links,
        root_event_id: null,
        updated_at: new Date().toISOString(),
      },
      episodes,
      long_term_summaries: [],
      active_episode_id: activeEpisode?.id ?? null,
      updated_at: new Date().toISOString(),
    };
  }

  async persistMemoryLayers(careRecipientId: string, layers: MemoryLayerStore): Promise<void> {
    const pool = await getPool();
    const client = await pool.connect();
    try {
      await client.query("BEGIN");

      for (const episode of layers.episodes) {
        await client.query(
          `INSERT INTO care_episodes (id, care_recipient_id, kind, status, summary, event_ids, opened_at, closed_at, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, NOW(), NOW())
           ON CONFLICT (id) DO UPDATE SET
             kind = $3, status = $4, summary = $5, event_ids = $6::jsonb,
             closed_at = $8, updated_at = NOW()`,
          [
            episode.id,
            careRecipientId,
            episode.kind,
            episode.status,
            episode.summary ?? null,
            JSON.stringify(episode.event_ids),
            episode.started_at,
            episode.ended_at ?? null,
          ],
        );
      }

      const existingLinksResult = await pool.query<{ id: string }>(
        `SELECT id FROM continuity_links WHERE care_recipient_id = $1`,
        [careRecipientId],
      );
      const existingIds = new Set(existingLinksResult.rows.map((r) => r.id));
      const newIds = new Set(layers.structured.links.map((l) => l.id));

      for (const id of existingIds) {
        if (!newIds.has(id)) {
          await client.query(`DELETE FROM continuity_links WHERE id = $1`, [id]);
        }
      }

      for (const link of layers.structured.links) {
        await client.query(
          `INSERT INTO continuity_links (id, care_recipient_id, from_event_id, to_event_id, link_type, note, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, NOW())
           ON CONFLICT (id) DO UPDATE SET
             from_event_id = $3, to_event_id = $4, link_type = $5, note = $6`,
          [
            link.id,
            careRecipientId,
            link.from_event_id,
            link.to_event_id,
            link.link_type,
            link.note,
          ],
        );
      }

      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  async getUnresolvedQuestions(careRecipientId: string): Promise<UnresolvedQuestion[]> {
    const pool = await getPool();
    const result = await pool.query<UnresolvedQuestionRow>(
      `SELECT id, care_recipient_id, question, source_event_id, status, resolved_at, resolution_event_id, created_at, updated_at
       FROM unresolved_questions WHERE care_recipient_id = $1 ORDER BY created_at DESC`,
      [careRecipientId],
    );
    return result.rows.map(mapUnresolvedRowToQuestion);
  }

  async persistUnresolvedQuestions(
    careRecipientId: string,
    questions: UnresolvedQuestion[],
  ): Promise<void> {
    const pool = await getPool();
    const client = await pool.connect();
    try {
      await client.query("BEGIN");

      const existingResult = await pool.query<{ id: string }>(
        `SELECT id FROM unresolved_questions WHERE care_recipient_id = $1`,
        [careRecipientId],
      );
      const existingIds = new Set(existingResult.rows.map((r) => r.id));
      const newIds = new Set(questions.map((q) => q.id));

      for (const id of existingIds) {
        if (!newIds.has(id)) {
          await client.query(`DELETE FROM unresolved_questions WHERE id = $1`, [id]);
        }
      }

      for (const q of questions) {
        await client.query(
          `INSERT INTO unresolved_questions (id, care_recipient_id, question, source_event_id, status, resolved_at, resolution_event_id, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW())
           ON CONFLICT (id) DO UPDATE SET
             question = $3, source_event_id = $4, status = $5,
             resolved_at = $6, resolution_event_id = $7, updated_at = NOW()`,
          [
            q.id,
            careRecipientId,
            q.question,
            q.sourceEventId ?? null,
            q.status,
            q.resolvedAt ?? null,
            q.resolutionEventId ?? null,
          ],
        );
      }

      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  async resolveQuestion(
    careRecipientId: string,
    questionId: string,
    resolutionEventId?: string,
  ): Promise<void> {
    const pool = await getPool();
    await pool.query(
      `UPDATE unresolved_questions SET status = 'resolved', resolved_at = NOW(), resolution_event_id = $3, updated_at = NOW()
       WHERE id = $1 AND care_recipient_id = $2`,
      [questionId, careRecipientId, resolutionEventId ?? null],
    );
  }
}

let singleton: PostgresCareRecordStore | null = null;

export function getPostgresCareRecordStore(): PostgresCareRecordStore {
  if (!singleton) {
    singleton = new PostgresCareRecordStore();
  }
  return singleton;
}
