/**
 * Community reputation system.
 *
 * Reputation is based on utility signals — bookmarks, saves, and
 * appointment-prep usage — not raw likes. This aligns with the
 * principle that community content should create lasting value.
 */
import type { CommunityPost, CommunityReaction, CommunityAuthorType } from "./types";

export type ReputationSignal =
  | "bookmark"
  | "save"
  | "prep_used"
  | "thank_you"
  | "support"
  | "like"
  | "caregiver_answer"
  | "caregiver_helpful"
  | "flag_received";

export type ReputationWeight = {
  bookmark: 3;
  save: 3;
  prep_used: 5;
  thank_you: 2;
  support: 2;
  like: 1;
  caregiver_answer: 10;
  caregiver_helpful: 2;
  flag_received: -10;
};

export const REPUTATION_WEIGHTS: ReputationWeight = {
  bookmark: 3,
  save: 3,
  prep_used: 5,
  thank_you: 2,
  support: 2,
  like: 1,
  caregiver_answer: 10,
  caregiver_helpful: 2,
  flag_received: -10,
};

export const TRUSTED_CAREGIVER_THRESHOLD = 50;

export type CaregiverReputation = {
  caregiver_id: string;
  total_score: number;
  signal_counts: Record<ReputationSignal, number>;
  trusted_caregiver: boolean;
  badge_level: "peer" | "contributor" | "trusted";
  first_contribution: string | null;
  last_activity: string | null;
  posts_count: number;
};

const reputationIndex = new Map<string, Map<ReputationSignal, number>>();
const firstContribution = new Map<string, string>();
const lastActivity = new Map<string, string>();

function ensureIndex(caregiverId: string): Map<ReputationSignal, number> {
  let idx = reputationIndex.get(caregiverId);
  if (!idx) {
    idx = new Map<ReputationSignal, number>();
    reputationIndex.set(caregiverId, idx);
  }
  return idx;
}

export function recordReputationSignal(
  caregiverId: string,
  signal: ReputationSignal,
  ts: string = new Date().toISOString(),
): void {
  const idx = ensureIndex(caregiverId);
  const current = idx.get(signal) ?? 0;
  idx.set(signal, current + 1);

  if (!firstContribution.has(caregiverId)) {
    firstContribution.set(caregiverId, ts);
  }
  lastActivity.set(caregiverId, ts);
}

export function computeReputation(caregiverId: string, postCount: number): CaregiverReputation {
  const idx = reputationIndex.get(caregiverId) ?? new Map();
  const signal_counts: Record<ReputationSignal, number> = {
    bookmark: idx.get("bookmark") ?? 0,
    save: idx.get("save") ?? 0,
    prep_used: idx.get("prep_used") ?? 0,
    thank_you: idx.get("thank_you") ?? 0,
    support: idx.get("support") ?? 0,
    like: idx.get("like") ?? 0,
    caregiver_answer: idx.get("caregiver_answer") ?? 0,
    caregiver_helpful: idx.get("caregiver_helpful") ?? 0,
    flag_received: idx.get("flag_received") ?? 0,
  };

  let total_score = 0;
  for (const [signal, count] of Object.entries(signal_counts)) {
    total_score += count * REPUTATION_WEIGHTS[signal as ReputationSignal];
  }

  total_score = Math.max(0, total_score);

  const trusted = total_score >= TRUSTED_CAREGIVER_THRESHOLD && postCount >= 3;

  let badge_level: "peer" | "contributor" | "trusted" = "peer";
  if (trusted) badge_level = "trusted";
  else if (total_score >= 15 || postCount >= 3) badge_level = "contributor";

  return {
    caregiver_id: caregiverId,
    total_score,
    signal_counts,
    trusted_caregiver: trusted,
    badge_level,
    first_contribution: firstContribution.get(caregiverId) ?? null,
    last_activity: lastActivity.get(caregiverId) ?? null,
    posts_count: postCount,
  };
}

export function deriveBadgeLabel(
  authorType: CommunityAuthorType,
  reputation: CaregiverReputation | null,
): { emoji: string; label: string; trust_level: "peer_caregiver" | "community_contributor" | "trusted_caregiver" | "professional" | "solenos_verified" } {
  if (authorType === "solenos_verified") {
    return { emoji: "🛡️", label: "SolenOS", trust_level: "solenos_verified" };
  }
  if (authorType === "professional" || authorType === "professional_pending") {
    return { emoji: "👩‍⚕️", label: "Verified Professional", trust_level: "professional" };
  }

  if (reputation?.trusted_caregiver) {
    return { emoji: "⭐", label: "Trusted Caregiver", trust_level: "trusted_caregiver" };
  }

  if (reputation?.badge_level === "contributor") {
    return { emoji: "❤️", label: "Community Contributor", trust_level: "community_contributor" };
  }

  return { emoji: "❤️", label: "Caregiver", trust_level: "peer_caregiver" };
}

export function resetReputationStore(): void {
  reputationIndex.clear();
  firstContribution.clear();
  lastActivity.clear();
}

export function getReputationForCaregivers(
  caregiverIds: string[],
  postCounts: Record<string, number>,
): Record<string, CaregiverReputation> {
  const result: Record<string, CaregiverReputation> = {};
  for (const id of caregiverIds) {
    result[id] = computeReputation(id, postCounts[id] ?? 0);
  }
  return result;
}
