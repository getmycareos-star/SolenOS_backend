import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { seedCommunityPosts } from "@/lib/community";

/** POST /api/community/seed — populate demo dementia community posts */
export async function POST(req: NextRequest) {
  const caregiverId =
    req.nextUrl.searchParams.get("caregiver_id") ?? "default_caregiver";

  const posts = seedCommunityPosts(caregiverId);
  return NextResponse.json({
    identity: "SolenOS community seed",
    seeded: posts.length,
    posts: posts.map((p) => ({
      id: p.id,
      title: p.title,
      author_type: p.author_type,
      author_label: p.author_label,
    })),
  });
}
