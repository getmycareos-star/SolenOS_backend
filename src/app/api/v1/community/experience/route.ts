import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { listPublicPosts, findSimilarExperiences, COMMUNITY_TAGS } from "@/lib/community";
import type { CommunityTag, CommunityCareStage } from "@/lib/community";

function isValidEnum<T extends readonly string[]>(arr: T, val: unknown): val is T[number] {
  return typeof val === "string" && (arr as readonly string[]).includes(val);
}

/** POST /api/v1/community/experience — find similar caregiver experiences for an observation */
export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  if (!body || typeof body !== "object") {
    return NextResponse.json({ error: "Request body must be an object" }, { status: 400 });
  }

  const input = body as Record<string, unknown>;
  const observation = typeof input.observation === "string" ? input.observation : null;
  const careStage =
    typeof input.care_stage === "string"
      ? (input.care_stage as string)
      : undefined;
  const tags = Array.isArray(input.tags)
    ? input.tags.filter((t): t is CommunityTag => typeof t === "string" && isValidEnum(COMMUNITY_TAGS, t))
    : undefined;
  const limit = typeof input.limit === "number" ? input.limit : 5;

  const posts = listPublicPosts({
    tags: tags,
    care_stages: careStage ? [careStage as CommunityCareStage] : undefined,
    limit: 500,
  });

  if (!observation) {
    return NextResponse.json({
      identity: "SolenOS community wisdom",
      boundary: "Peer experience, not medical advice.",
      matches: [],
    });
  }

  const result = findSimilarExperiences(observation, posts, careStage as any, limit);

  return NextResponse.json({
    identity: "SolenOS community wisdom",
    boundary:
      "These are peer experiences, not medical advice. Always confirm care decisions with a clinician.",
    trigger_observation: result.trigger_observation,
    matched_keywords: result.matched_keywords,
    matches: result.matches.map((m) => ({
      post_id: m.post_id,
      author_label: m.author_label,
      author_type: m.author_type,
      content: m.body_preview,
      tags: m.matched_tags,
      relevance_score: m.relevance_score,
    })),
    community_pattern: result.community_pattern,
  });
}
