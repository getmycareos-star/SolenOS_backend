import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { listUnansweredQuestions, listDistressPosts } from "@/lib/community";

/** GET /api/v1/community/help-requests — list unanswered questions needing input */
export async function GET(req: NextRequest) {
  const limit = req.nextUrl.searchParams.get("limit");
  const distressOnly = req.nextUrl.searchParams.get("distress_only") === "true";

  const questions = distressOnly
    ? listDistressPosts().map((p) => ({
        question_id: p.id,
        post_id: p.id,
        content: p.body,
        author_label: p.author_label,
        created_at: p.created_at,
        tags: p.tags,
        community_answer: null,
        distress_detected: true,
      }))
    : listUnansweredQuestions(limit ? parseInt(limit, 10) : 20).map((p) => ({
        question_id: p.id,
        post_id: p.id,
        content: p.body,
        author_label: p.author_label,
        created_at: p.created_at,
        tags: p.tags,
        community_answer: null,
        distress_detected: p.moderation_reason === "distress_signal",
      }));

  return NextResponse.json({
    identity: "SolenOS community — peer support for caregivers",
    boundary: "These are peer-shared questions, not medical advice requests.",
    requests: questions,
  });
}
