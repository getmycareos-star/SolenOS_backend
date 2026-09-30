import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import {
  getProfessionalProfile,
  professionalSummary,
  approveVerification,
  listVerifiedProfessionals,
} from "@/lib/community";
import { assertOpsAccess } from "@/lib/ops-console/access";

function authFromReq(req: NextRequest): string | null {
  return (
    req.headers.get("authorization")?.replace(/^Bearer\s+/i, "") ??
    req.nextUrl.searchParams.get("key") ??
    req.nextUrl.searchParams.get("ops_key") ??
    null
  );
}

/** GET /api/v1/community/professional/[id] — get professional profile */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const profile = getProfessionalProfile(id);
  if (!profile) {
    return NextResponse.json({ error: "Profile not found" }, { status: 404 });
  }

  const includeImpact = req.nextUrl.searchParams.get("include_impact") === "true";
  const summary = professionalSummary(profile);

  return NextResponse.json({
    profile: includeImpact ? profile : summary,
  });
}

/** PATCH /api/v1/community/professional/[id] — approve verification (team only) */
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const key = authFromReq(req);
  if (!assertOpsAccess(key)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 404 });
  }

  const { id } = await params;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const input = (body ?? {}) as Record<string, unknown>;

  if (input.action === "approve") {
    const profile = approveVerification(id, typeof input.note === "string" ? input.note : null);
    if (!profile) {
      return NextResponse.json({ error: "Profile not found" }, { status: 404 });
    }
    return NextResponse.json({
      profile: professionalSummary(profile),
      message: "Professional verified successfully.",
    });
  }

  return NextResponse.json({ error: "Unknown action" }, { status: 400 });
}
