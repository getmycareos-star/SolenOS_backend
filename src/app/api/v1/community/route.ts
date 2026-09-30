import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import {
  COMMUNITY_AUTHOR_TYPES,
  COMMUNITY_CARE_STAGES,
  COMMUNITY_TAGS,
  COMMUNITY_VISIBILITY,
  COMMUNITY_POST_TYPES,
  type CommunityAuthorType,
  type CommunityCareStage,
  type CommunityTag,
  type CommunityVisibility,
  type PostType,
  createPost,
  getPost,
  listPublicPosts,
  updatePostModeration,
  updatePost,
  moderateContent,
  mergeTags,
  inferCareStage,
  getReactionCounts,
  getUserReaction,
  countCommentsForPost,
  seedCommunityPosts,
} from "@/lib/community";

const DEFAULT_CAREGIVER_ID = "default_caregiver";

function isValidEnum<T extends readonly string[]>(
  arr: T,
  val: unknown,
): val is T[number] {
  return typeof val === "string" && (arr as readonly string[]).includes(val);
}

function buildSummary(postId: string, caregiverId: string) {
  const post = getPost(postId);
  if (!post) return null;

  return {
    ...post,
    reaction_counts: getReactionCounts(postId),
    comment_count: countCommentsForPost(postId),
    user_reaction: getUserReaction(postId, caregiverId),
  };
}

/** GET /api/v1/community — list public posts (auto-seeds demo data if empty) */
export async function GET(req: NextRequest) {
  const caregiverId =
    req.nextUrl.searchParams.get("caregiver_id") ?? DEFAULT_CAREGIVER_ID;

  const tags = req.nextUrl.searchParams.get("tags");
  const stages = req.nextUrl.searchParams.get("care_stages");
  const limit = req.nextUrl.searchParams.get("limit");

  const tagArray = tags
    ? tags.split(",").filter((t): t is CommunityTag => isValidEnum(COMMUNITY_TAGS, t))
    : undefined;

  const stageArray = stages
    ? stages.split(",").filter((s): s is CommunityCareStage => isValidEnum(COMMUNITY_CARE_STAGES, s))
    : undefined;

  const limitNum = limit ? parseInt(limit, 10) : undefined;

  let posts = listPublicPosts({
    tags: tagArray,
    care_stages: stageArray,
    limit: limitNum,
  });

  if (posts.length === 0) {
    seedCommunityPosts(caregiverId);
    posts = listPublicPosts({
      tags: tagArray,
      care_stages: stageArray,
      limit: limitNum,
    });
  }

  return NextResponse.json({
    identity: "SolenOS community — shared experience, not medical advice.",
    boundary: "Community insights are peer experience. Confirm all care decisions with a clinician.",
    posts: posts.map((p) => buildSummary(p.id, caregiverId)),
  });
}

/** POST /api/v1/community — create a new post */
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
  const caregiverId =
    (typeof input.caregiver_id === "string" && input.caregiver_id.trim()) ||
    req.nextUrl.searchParams.get("caregiver_id") ||
    DEFAULT_CAREGIVER_ID;

  const content = input.content;
  if (typeof content !== "string" || !content.trim()) {
    return NextResponse.json({ error: "content must be a non-empty string" }, { status: 400 });
  }

  const authorType = isValidEnum(COMMUNITY_AUTHOR_TYPES, input.author_type)
    ? (input.author_type as CommunityAuthorType)
    : "peer_caregiver";

  const visibility = isValidEnum(COMMUNITY_VISIBILITY, input.visibility)
    ? (input.visibility as CommunityVisibility)
    : "public";

  let careStage: CommunityCareStage = "unspecified";
  if (isValidEnum(COMMUNITY_CARE_STAGES, input.care_stage)) {
    careStage = input.care_stage as CommunityCareStage;
  } else {
    careStage = inferCareStage(content);
  }

  const rawTags = Array.isArray(input.tags)
    ? input.tags.filter((t): t is CommunityTag => isValidEnum(COMMUNITY_TAGS, t))
    : [];

  const moderation = moderateContent(content, typeof input.title === "string" ? input.title : null, authorType);
  const allTags = mergeTags([] as string[], [...rawTags, ...moderation.auto_tags]);
  const tags = allTags.filter((t): t is CommunityTag => isValidEnum(COMMUNITY_TAGS, t));

  const media = Array.isArray(input.media)
    ? input.media.map((m): { url: string; mime_type: string; alt: string | null } => ({
        url: typeof m === "object" && m !== null && typeof (m as any).url === "string" ? (m as any).url : "",
        mime_type: typeof m === "object" && m !== null && typeof (m as any).mime_type === "string" ? (m as any).mime_type : "text/plain",
        alt: typeof m === "object" && m !== null && typeof (m as any).alt === "string" ? (m as any).alt : null,
      }))
    : [];

  const authorLabel =
    (typeof input.author_label === "string" && input.author_label.trim()) ||
    (authorType === "solenos_verified" ? "SolenOS" : "Anonymous caregiver");

  const postType = isValidEnum(COMMUNITY_POST_TYPES, input.post_type)
    ? (input.post_type as PostType)
    : "experience";

  const post = createPost({
    caregiver_id: caregiverId,
    author_type: authorType,
    author_label: authorLabel,
    post_type: postType,
    title: typeof input.title === "string" ? input.title.trim() : null,
    body: content,
    media,
    visibility,
    care_stage: careStage,
    tags,
  });

  updatePostModeration(post.id, {
    status: moderation.status,
    moderation_reason: moderation.reason,
    care_stage: careStage,
    tags,
  });

  const created = getPost(post.id);
  if (!created) {
    return NextResponse.json({ error: "Failed to create post" }, { status: 500 });
  }

  return NextResponse.json({
    identity: "SolenOS community — shared experience, not medical advice.",
    boundary: "Community insights are peer experience. Confirm all care decisions with a clinician.",
    post: created,
    summary: buildSummary(created.id, caregiverId),
    moderation: {
      status: moderation.status,
      reason: moderation.reason,
      distress_detected: moderation.distress_detected,
      risky_advice_detected: moderation.risky_advice_detected,
      auto_tags: moderation.auto_tags,
    },
  }, { status: 201 });
}
