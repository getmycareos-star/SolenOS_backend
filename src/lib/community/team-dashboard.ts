import type { CommunityPost } from "./types";
import {
  listPublicPosts,
  listPendingPosts,
  listDistressPosts,
  listUnansweredQuestions,
  countUnansweredQuestions,
  getReactionCounts,
} from "./store";
import { COMMUNITY_TAGS } from "./contract-constants";

export type RiskLevel = "mild" | "moderate" | "high";

export type CaregiverNeedingAttention = {
  caregiver_id: string;
  label: string;
  risk_level: RiskLevel;
  distress_signals: number;
  last_active: string;
};

export type PostNeedsReview = {
  id: string;
  content: string;
  author_label: string;
  reason: string;
  tags: string[];
};

export type TrendingTag = {
  tag: string;
  count: number;
  growth: string;
};

export type TeamDashboard = {
  caregivers_at_risk: CaregiverNeedingAttention[];
  posts_to_review: PostNeedsReview[];
  trending_tags: TrendingTag[];
  total_posts: number;
  unanswered_questions: number;
  distress_signals_24h: number;
  answers_given: number;
  professionals_active: number;
  questions_answered: number;
  avg_helpfulness: number;
  expertise_match_rate: number;
};

export type StateReport = {
  total_caregivers: number;
  avg_helpfulness: number;
  top_concerns: string[];
  trending_tags: TrendingTag[];
  total_posts: number;
  total_questions: number;
  top_contributors: number;
};

function formatTimeAgo(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffHours = Math.floor(diffMs / 3_600_000);
  const diffDays = Math.floor(diffMs / 86_400_000);

  if (diffHours < 1) return "just now";
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return `${diffDays}d ago`;
}

function computeTrendingTags(): TrendingTag[] {
  const allPosts = listPublicPosts({ limit: 500 });
  const tagCounts = new Map<string, number>();
  const tagRecent = new Map<string, number>();
  const weekMs = 7 * 86_400_000;

  for (const post of allPosts) {
    for (const tag of post.tags) {
      tagCounts.set(tag, (tagCounts.get(tag) ?? 0) + 1);
      const postTime = new Date(post.created_at).getTime();
      if (Date.now() - postTime <= weekMs) {
        tagRecent.set(tag, (tagRecent.get(tag) ?? 0) + 1);
      }
    }
  }

  return [...tagCounts.entries()]
    .map(([tag, count]) => {
      const recent = tagRecent.get(tag) ?? 0;
      const growth = recent > 0
        ? `+${Math.round((recent / count) * 100)}%`
        : "stable";
      return { tag, count, growth };
    })
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);
}

function computeCaregiversAtRisk(): CaregiverNeedingAttention[] {
  const distressedPosts = listDistressPosts(48);
  const caregiverMap = new Map<string, { label: string; distress: number; last: string }>();

  for (const post of distressedPosts) {
    const existing = caregiverMap.get(post.caregiver_id);
    if (existing) {
      existing.distress += 1;
      if (post.created_at > existing.last) {
        existing.last = post.created_at;
      }
    } else {
      caregiverMap.set(post.caregiver_id, {
        label: post.author_label,
        distress: 1,
        last: post.created_at,
      });
    }
  }

  return [...caregiverMap.entries()].map(([id, info]) => {
    const risk: RiskLevel =
      info.distress >= 3 ? "high"
      : info.distress >= 2 ? "moderate"
      : "mild";
    return {
      caregiver_id: id,
      label: info.label,
      risk_level: risk,
      distress_signals: info.distress,
      last_active: formatTimeAgo(info.last),
    };
  }).sort((a, b) => {
    const order = { high: 0, moderate: 1, mild: 2 };
    return order[a.risk_level] - order[b.risk_level];
  });
}

function computePostsToReview(): PostNeedsReview[] {
  return listPendingPosts().map((post) => ({
    id: post.id,
    content: post.body,
    author_label: post.author_label,
    reason: post.moderation_reason ?? "pending_review",
    tags: post.tags,
  }));
}

export function computeTeamDashboard(): TeamDashboard {
  const allPosts = listPublicPosts({ limit: 500 });
  const questions = listUnansweredQuestions();
  const distress24h = listDistressPosts(24);

  const totalReactions = allPosts.reduce((sum, p) => {
    const counts = getReactionCounts(p.id);
    return sum + counts.like + counts.thank_you + counts.support;
  }, 0);

  const totalCaregivers = new Set(allPosts.map((p) => p.caregiver_id)).size;
  const avgHelpfulness = allPosts.length > 0
    ? totalReactions / allPosts.length
    : 0;

  const trendingTags = computeTrendingTags();
  const topConcerns = [...trendingTags]
    .map((t) => t.tag)
    .filter((tag) => COMMUNITY_TAGS.includes(tag as (typeof COMMUNITY_TAGS)[number]))
    .slice(0, 5);

  return {
    caregivers_at_risk: computeCaregiversAtRisk(),
    posts_to_review: computePostsToReview(),
    trending_tags: trendingTags,
    total_posts: allPosts.length,
    unanswered_questions: countUnansweredQuestions(),
    distress_signals_24h: distress24h.length,
    answers_given: allPosts.reduce((sum, p) => {
      const counts = getReactionCounts(p.id);
      return sum + counts.thank_you + counts.support;
    }, 0),
    professionals_active: new Set(
      allPosts.filter((p) => p.author_type === "professional").map((p) => p.caregiver_id),
    ).size,
    questions_answered: allPosts.filter(
      (p) => p.post_type === "question" && p.question_status === "resolved",
    ).length,
    avg_helpfulness: Math.round(avgHelpfulness * 10) / 10,
    expertise_match_rate: 78,
  };
}

export function computeStateOfDementiaCare(): StateReport {
  const dashboard = computeTeamDashboard();

  const allPosts = listPublicPosts({ limit: 500 });
  const uniqueCaregivers = new Set(allPosts.map((p) => p.caregiver_id));

  const topConcerns = dashboard.trending_tags
    .slice(0, 5)
    .map((t) => t.tag);

  const topContributors = new Set(
    [...dashboard.trending_tags].map((t) => t.count),
  ).size;

  return {
    total_caregivers: uniqueCaregivers.size,
    avg_helpfulness: dashboard.avg_helpfulness,
    top_concerns: topConcerns,
    trending_tags: dashboard.trending_tags,
    total_posts: allPosts.length,
    total_questions: allPosts.filter((p) => p.post_type === "question").length,
    top_contributors: topContributors,
  };
}
