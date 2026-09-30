import { NextResponse } from "next/server";
import { seedCommunityPosts, resetCommunityStore, resetReputationStore } from "@/lib/community";

/** POST /api/v1/community/seed — seed demo community data */
export async function POST() {
  resetCommunityStore();
  resetReputationStore();
  const posts = seedCommunityPosts("demo-caregiver");
  return NextResponse.json({
    identity: "SolenOS community seeding",
    boundary: "Demo data for development and testing.",
    posts_seeded: posts.length,
    post_ids: posts.map((p) => p.id),
  }, { status: 201 });
}
