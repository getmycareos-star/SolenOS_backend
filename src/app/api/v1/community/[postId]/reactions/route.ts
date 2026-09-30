import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import {
  REACTION_TYPES,
  COMMUNITY_AUTHOR_TYPES,
  type ReactionType,
  type CommunityAuthorType,
  getPost,
  createReaction,
  getReactionsForPost,
  getUserReaction,
  getReactionCounts,
  recordReputationSignal,
} from "@/lib/community";

function isValidEnum<T extends readonly string[]>(
  arr: T,
  val: unknown,
): val is T[number] {
  return typeof val === "string" && (arr as readonly string[]).includes(val);
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ postId: string }> },
) {
  const { postId } = await params;
  const post = getPost(postId);
  if (!post) {
    return NextResponse.json({ error: "Post not found" }, { status: 404 });
  }

  const reactions = getReactionsForPost(postId);
  return NextResponse.json({
    post_id: postId,
    reactions,
    total: reactions.length,
    summary: getReactionCounts(postId),
  });
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ postId: string }> },
) {
  const { postId } = await params;
  const post = getPost(postId);
  if (!post) {
    return NextResponse.json({ error: "Post not found" }, { status: 404 });
  }

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

  if (!isValidEnum(REACTION_TYPES, input.reaction_type)) {
    return NextResponse.json({ error: "Invalid reaction_type" }, { status: 400 });
  }
  const reactionType = input.reaction_type as ReactionType;

  const caregiverId =
    typeof input.caregiver_id === "string" && input.caregiver_id.trim()
      ? input.caregiver_id
      : "default_caregiver";

  const authorType = isValidEnum(COMMUNITY_AUTHOR_TYPES, input.author_type)
    ? (input.author_type as CommunityAuthorType)
    : "peer_caregiver";

  const existing = getUserReaction(postId, caregiverId);
  if (existing) {
    return NextResponse.json({
      error: "Reaction already exists",
      existing_reaction: existing,
    }, { status: 409 });
  }

  const reaction = createReaction({
    post_id: postId,
    caregiver_id: caregiverId,
    author_type: authorType,
    reaction_type: reactionType,
  });

  recordReputationSignal(post.caregiver_id, reactionType);

  return NextResponse.json({ reaction, summary: getReactionCounts(postId) }, { status: 201 });
}
