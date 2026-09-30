/**
 * Similar Caregiver Experiences engine.
 *
 * When a caregiver logs an observation like "Dad wandered at 2am",
 * this module finds community posts from caregivers who experienced
 * similar symptoms in the same care stage. Returns ranked wisdom
 * suggestions surfaced alongside LCR observations.
 */
import type { CommunityPost } from "./types";
import type { CommunityCareStage } from "./types";

export type ExperienceMatch = {
  post_id: string;
  title: string | null;
  body_preview: string;
  author_label: string;
  author_type: string;
  care_stage: CommunityCareStage;
  matched_tags: string[];
  relevance_score: number;
  reason: string;
};

export type SimilarExperiencesResult = {
  trigger_observation: string;
  matched_keywords: string[];
  matches: ExperienceMatch[];
  community_pattern: string | null;
};

const SYMPTOM_KEYWORDS: Record<string, { keywords: RegExp[]; tag: string; label: string }> = {
  wandering: {
    keywords: [/\bwander\b/i, /\belop(?:e)?\b/i, /\b(?:got|getting)\s+(?:out|away)\b/i, /\bexit-seek/i, /\bleaving\b/i],
    tag: "wandering",
    label: "nighttime wandering",
  },
  sundowning: {
    keywords: [/\bsundown/i, /\bevening/i, /\bafternoon/i, /\bagitat/i, /\bpacing\b/i],
    tag: "sundowning",
    label: "evening agitation",
  },
  medication: {
    keywords: [/\bmed(?:ication|s)?\b/i, /\bpill\b/i, /\brefus/i, /\bmissed/i, /\badherence\b/i, /\bdose\b/i, /\bpharmacy\b/i],
    tag: "medication",
    label: "medication issues",
  },
  behavior: {
    keywords: [/\bagitat/i, /\bcombative\b/i, /\bresist/i, /\byell/i, /\bshout\b/i, /\bbehavior\b/i],
    tag: "behavior",
    label: "challenging behavior",
  },
  sleep: {
    keywords: [/\bsleep\b/i, /\bnight/i, /\binsomnia\b/i, /\bwaking\b/i, /\brestless\b/i],
    tag: "sleep",
    label: "sleep disruption",
  },
  communication: {
    keywords: [/\btalk\b/i, /\bspeak\b/i, /\bword\b/i, /\bcommunicat/i, /\blanguage\b/i, /\brepeating\b/i, /\bask.*same\b/i],
    tag: "communication",
    label: "communication changes",
  },
  mobility: {
    keywords: [/\bwalk/i, /\bmobil/i, /\bstair\b/i, /\bfall(?:en|ing)?\b/i, /\bstumbl/i, /\bunsteady\b/i, /\bgait\b/i],
    tag: "mobility",
    label: "mobility concerns",
  },
  nutrition: {
    keywords: [/\beat\b/i, /\bfood\b/i, /\bmeal\b/i, /\bappetite\b/i, /\bweight\b/i, /\bdrink\b/i, /\bhydrat/i],
    tag: "nutrition",
    label: "nutrition/appetite",
  },
  safety: {
    keywords: [/\bsafe/i, /\block\b/i, /\bgate\b/i, /\bmonitor/i, /\bsupervis/i, /\bdoor\b/i],
    tag: "safety",
    label: "safety concerns",
  },
  hallucination: {
    keywords: [/\bhallucin/i, /\bdelusion/i, /\bseeing things\b/i, /\bparanoid\b/i],
    tag: "general",
    label: "perceptual changes",
  },
};

export function extractObservationKeywords(observation: string): {
  tags: string[];
  labels: string[];
  stage_hint: string | null;
} {
  const lower = observation.toLowerCase();
  const matchedTags: string[] = [];
  const matchedLabels: string[] = [];

  for (const [, config] of Object.entries(SYMPTOM_KEYWORDS)) {
    if (config.keywords.some((re) => re.test(lower))) {
      if (!matchedTags.includes(config.tag)) {
        matchedTags.push(config.tag);
      }
      if (!matchedLabels.includes(config.label)) {
        matchedLabels.push(config.label);
      }
    }
  }

  let stageHint: string | null = null;
  if (/\b(early|first|just|newly)\b/i.test(lower)) {
    stageHint = "early";
  } else if (/\b(getting|became|worsen|deteriorat|severe|advanced)\b/i.test(lower)) {
    stageHint = "late";
  }

  return { tags: matchedTags, labels: matchedLabels, stage_hint: stageHint };
}

export function findSimilarExperiences(
  observation: string,
  posts: CommunityPost[],
  careStage?: CommunityCareStage,
  limit: number = 5,
): SimilarExperiencesResult {
  const { tags: matchedTags, labels: matchedLabels, stage_hint } =
    extractObservationKeywords(observation);

  const allPosts = posts.filter((p) => p.status === "approved");

  const scored: ExperienceMatch[] = [];

  for (const post of allPosts) {
    const postTags = post.tags;
    const matchedTagsInPost = matchedTags.filter((t) => postTags.some((pt) => pt === t));

    if (matchedTagsInPost.length === 0) continue;

    let score = 0;
    const reasons: string[] = [];

    score += matchedTagsInPost.length * 30;
    if (matchedTagsInPost.length > 0) {
      reasons.push(`matched on ${matchedTagsInPost.join(", ")}`);
    }

    if (careStage && post.care_stage === careStage) {
      score += 20;
      reasons.push(`same care stage (${careStage})`);
    } else if (stage_hint && post.care_stage === stage_hint) {
      score += 10;
      reasons.push(`similar stage to observation`);
    }

    const bodyLower = post.body.toLowerCase();
    const obsLower = observation.toLowerCase();
    const obsWords = obsLower
      .replace(/[^\w\s]/g, "")
      .split(/\s+/)
      .filter((w) => w.length > 3);

    let keywordOverlap = 0;
    for (const word of obsWords) {
      if (bodyLower.includes(word)) {
        keywordOverlap++;
      }
    }
    if (keywordOverlap > 0) {
      score += Math.min(keywordOverlap * 5, 15);
      reasons.push(`shared language (${keywordOverlap} terms)`);
    }

    if (post.author_type === "solenos_verified" || post.author_type === "professional") {
      score += 15;
      reasons.push("verified contributor");
    }

    const hoursSincePost = (Date.now() - new Date(post.created_at).getTime()) / 3600000;
    if (hoursSincePost < 24) {
      score += 10;
    } else if (hoursSincePost < 168) {
      score += 5;
    }

    const preview = post.body.length > 160 ? post.body.slice(0, 160) + "…" : post.body;

    scored.push({
      post_id: post.id,
      title: post.title,
      body_preview: preview,
      author_label: post.author_label,
      author_type: post.author_type,
      care_stage: post.care_stage,
      matched_tags: matchedTagsInPost,
      relevance_score: Math.round(score),
      reason: reasons.join("; "),
    });
  }

  scored.sort((a, b) => b.relevance_score - a.relevance_score);

  const topMatches = scored.slice(0, limit);

  let communityPattern: string | null = null;
  if (topMatches.length >= 3) {
    const commonTags = matchedTags.filter((tag) =>
      topMatches.every((m) => m.matched_tags.includes(tag)),
    );
    if (commonTags.length > 0) {
      communityPattern = `Community pattern: ${matchedLabels.join(", ")} frequently appeared alongside ${commonTags.map((t) => `"${t}"`).join(" and ")}. Caregivers found that ${
        commonTags.includes("wandering") ? "environmental modifications and door alarms" :
        commonTags.includes("sundowning") ? "dimming lights before evening and calming music" :
        commonTags.includes("medication") ? "consistency in administration timing" :
        commonTags.includes("behavior") ? "validation over correction reduced conflict" :
        "caregiver communities found shared routines helpful"
      }.`;
    }
  }

  return {
    trigger_observation: observation,
    matched_keywords: matchedLabels,
    matches: topMatches,
    community_pattern: communityPattern,
  };
}
