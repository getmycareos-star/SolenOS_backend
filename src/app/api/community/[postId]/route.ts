import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import {
  COMMUNITY_POST_STATUSES,
  COMMUNITY_VISIBILITY,
  type CommunityPostStatus,
  type CommunityTag,
  type CommunityCareStage,
  type CommunityVisibility,
  getPost,
  updatePost,
  resetCommunityStore,
  getReactionCounts,
  getUserReaction,
  countCommentsForPost,
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
  const caregiverId =
    req.nextUrl.searchParams.get("caregiver_id") ?? "default_caregiver";

  const post = getPost(postId);
  if (!post) {
    return NextResponse.json({ error: "Post not found" }, { status: 404 });
  }

  return NextResponse.json({
    post,
    summary: {
      ...post,
      reaction_counts: getReactionCounts(postId),
      comment_count: countCommentsForPost(postId),
      user_reaction: getUserReaction(postId, caregiverId),
    },
  });
}

export async function PATCH(
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

  const input = body as Record<string, string | unknown>;

  const patch: Record<string, unknown> = {};

  if (input.status !== undefined) {
    if (!isValidEnum(COMMUNITY_POST_STATUSES, input.status)) {
      return NextResponse.json({ error: "Invalid status" }, { status: 400 });
    }
    patch.status = input.status as CommunityPostStatus;
  }

  if (input.moderation_reason !== undefined) {
    patch.moderation_reason =
      input.moderation_reason === null ? null : String(input.moderation_reason);
  }

  if (input.visibility !== undefined) {
    if (!isValidEnum(COMMUNITY_VISIBILITY, input.visibility)) {
      return NextResponse.json({ error: "Invalid visibility" }, { status: 400 });
    }
    patch.visibility = input.visibility as CommunityVisibility;
  }

  if (input.care_stage !== undefined) {
    if (typeof input.care_stage === "string") {
      patch.care_stage = input.care_stage as CommunityCareStage;
    }
  }

  if (input.tags !== undefined && Array.isArray(input.tags)) {
    patch.tags = input.tags.filter((t): t is CommunityTag => typeof t === "string");
  }

  if (Object.keys(patch).length === 0) {
    return NextResponse.json({ error: "No valid fields to update" }, { status: 400 });
  }

  const updated = updatePost(postId, patch as Parameters<typeof updatePost>[1]);
  if (!updated) {
    return NextResponse.json({ error: "Failed to update post" }, { status: 500 });
  }

  return NextResponse.json({ post: updated });
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ postId: string }> },
) {
  const { postId } = await params;

  if (postId === "__reset__") {
    resetCommunityStore();
    return NextResponse.json({ ok: true });
  }

  const post = getPost(postId);
  if (!post) {
    return NextResponse.json({ error: "Post not found" }, { status: 404 });
  }

  return NextResponse.json({ deleted: postId });
}
