import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { updatePostModeration, getPost } from "@/lib/community";
import type { CommunityPostStatus } from "@/lib/community";

const DEFAULT_CAREGIVER_ID = "default_caregiver";

function authFromReq(req: NextRequest): string | null {
  return (
    req.headers.get("authorization")?.replace(/^Bearer\s+/i, "") ??
    req.nextUrl.searchParams.get("key") ??
    req.nextUrl.searchParams.get("caregiver_id") ??
    null
  );
}

/** PATCH /api/v1/community/{postId}/moderate — moderate a post */
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ postId: string }> },
) {
  return moderatePost(req, await params);
}

/** POST /api/v1/community/{postId}/moderate — moderate a post (alias) */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ postId: string }> },
) {
  return moderatePost(req, await params);
}

async function moderatePost(
  req: NextRequest,
  { postId }: { postId: string },
) {
  const key = authFromReq(req);
  if (key !== DEFAULT_CAREGIVER_ID) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  if (!getPost(postId)) {
    return NextResponse.json({ error: "Post not found" }, { status: 404 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const input = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;

  const status = typeof input.status === "string" ? input.status : "approved";
  const reason =
    typeof input.reason === "string" ? input.reason.trim() :
    typeof input.moderation_reason === "string" ? input.moderation_reason.trim() :
    null;

  const updated = updatePostModeration(postId, {
    status: status as CommunityPostStatus,
    moderation_reason: reason,
  });

  if (!updated) {
    return NextResponse.json({ error: "Failed to moderate post" }, { status: 500 });
  }

  return NextResponse.json({
    identity: "SolenOS community moderation",
    post: updated,
  });
}
