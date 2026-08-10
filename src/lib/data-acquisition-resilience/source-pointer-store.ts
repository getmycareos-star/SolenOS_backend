import type { ClaimDowngradeRecord } from "./types";

/**
 * In-memory downgrade log.
 *
 * Persistence reality: DARE runtime persistence is currently in-memory. This
 * store holds the downgrade history for the running process. When a Postgres
 * write path is added for the dare_claim_downgrades table (migration
 * 076_source_pointer_trust.sql), hook `persistDowngrade` to INSERT there.
 *
 * The downgrade history is valuable product-quality data:
 *  - how often the extractor invents unsupported source spans
 *  - which document types / extraction methods are unreliable
 *  - whether model/prompt changes improve or degrade provenance quality
 */

const downgradeLog: ClaimDowngradeRecord[] = [];

export function recordDowngrade(record: ClaimDowngradeRecord): void {
  downgradeLog.push(record);
  // Optional Postgres persistence hook (see migration 076):
  // persistDowngrade(record).catch(() => { /* non-fatal */ });
}

export function listDowngrades(): ClaimDowngradeRecord[] {
  return [...downgradeLog];
}

export function listDowngradesForClaim(claimId: string): ClaimDowngradeRecord[] {
  return downgradeLog.filter((r) => r.claim_id === claimId);
}

export function clearDowngradeLog(): void {
  downgradeLog.length = 0;
}

/**
 * Optional Postgres persistence hook. Kept documented but inert so the app
 * does not open a DB connection that is not part of the active runtime path.
 */
export async function persistDowngrade(_record: ClaimDowngradeRecord): Promise<void> {
  // Intentionally a no-op until a live DARE Postgres write path exists.
  // Adapter would use the pg Pool to INSERT INTO dare_claim_downgrades (...).
  return;
}
