import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import {
  confirmProposedMeeting,
  getMeeting,
  prepareMeetingNow,
  recordMeetingOutcome,
  toMeetingPreparationLayerPayload,
} from "@/lib/meeting-preparation";
import {
  listPublicPosts,
  findSimilarExperiences,
} from "@/lib/community";

function findCommunityWisdom(whatChanged: string[]): {
  change: string;
  matches: {
    post_id: string;
    author_label: string;
    author_type: string;
    content: string;
    tags: string[];
  }[];
}[] {
  if (!whatChanged || whatChanged.length === 0) return [];
  const posts = listPublicPosts({ limit: 100 });
  return whatChanged
    .map((change) => {
      const result = findSimilarExperiences(change, posts, undefined, 3);
      return {
        change,
        matches: result.matches.map((m) => ({
          post_id: m.post_id,
          author_label: m.author_label,
          author_type: m.author_type,
          content: m.body_preview,
          tags: m.matched_tags,
        })),
      };
    })
    .filter((w) => w.matches.length > 0);
}

/** POST /api/meetings/prepare — generate preparation pack now */
export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const input = (body ?? {}) as Record<string, unknown>;
  const meetingId = input.meeting_id;

  if (typeof meetingId !== "string" || !meetingId.trim()) {
    return NextResponse.json({ error: "meeting_id required" }, { status: 400 });
  }

  const meeting = prepareMeetingNow(meetingId.trim());
  if (!meeting) {
    return NextResponse.json(
      { error: "Meeting not found or not eligible for preparation" },
      { status: 404 },
    );
  }

  return NextResponse.json({
    meeting,
    layer: toMeetingPreparationLayerPayload(meeting),
    identity: "SolenOS Appointment Preparation",
    boundary: "Community wisdom shows how other caregivers handled similar situations. Peer experiences are not medical advice.",
    community_wisdom: findCommunityWisdom(
      meeting.preparation_pack?.what_changed ?? [],
    ),
  });
}

/** PATCH /api/meetings/prepare — confirm proposed meeting */
export async function PATCH(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const input = (body ?? {}) as Record<string, unknown>;
  const meetingId = input.meeting_id;

  if (typeof meetingId !== "string") {
    return NextResponse.json({ error: "meeting_id required" }, { status: 400 });
  }

  const meeting = confirmProposedMeeting(meetingId);
  if (!meeting) {
    return NextResponse.json({ error: "Proposed meeting not found" }, { status: 404 });
  }

  return NextResponse.json({ meeting });
}

/** PUT /api/meetings/prepare — complete meeting with outcome */
export async function PUT(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const input = (body ?? {}) as Record<string, unknown>;
  const meetingId = input.meeting_id;

  if (typeof meetingId !== "string") {
    return NextResponse.json({ error: "meeting_id required" }, { status: 400 });
  }

  const outcome = input.outcome;
  if (!outcome || typeof outcome !== "object") {
    return NextResponse.json({ error: "outcome object required" }, { status: 400 });
  }

  const o = outcome as Record<string, unknown>;
  const strArray = (v: unknown) =>
    Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];

  const meeting = recordMeetingOutcome({
    meeting_id: meetingId,
    outcome: {
      decisions_made: strArray(o.decisions_made),
      advice_received: strArray(o.advice_received),
      responsibilities_assigned: strArray(o.responsibilities_assigned),
      follow_up_actions: strArray(o.follow_up_actions),
      new_questions: strArray(o.new_questions),
      documents_received: strArray(o.documents_received),
      deadlines_created: strArray(o.deadlines_created),
      notes: typeof o.notes === "string" ? o.notes : undefined,
    },
  });

  if (!meeting) {
    return NextResponse.json({ error: "Meeting not found" }, { status: 404 });
  }

  return NextResponse.json({ meeting });
}

/** GET /api/meetings/prepare?meeting_id= */
export async function GET(req: NextRequest) {
  const meetingId = req.nextUrl.searchParams.get("meeting_id");
  if (!meetingId) {
    return NextResponse.json({ error: "meeting_id required" }, { status: 400 });
  }

  const meeting = getMeeting(meetingId);
  if (!meeting) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }

  return NextResponse.json({
    meeting,
    layer: toMeetingPreparationLayerPayload(meeting),
    identity: "SolenOS Appointment Preparation",
    boundary: "Community wisdom shows how other caregivers handled similar situations. Peer experiences are not medical advice.",
    community_wisdom: findCommunityWisdom(
      meeting.preparation_pack?.what_changed ?? [],
    ),
  });
}
