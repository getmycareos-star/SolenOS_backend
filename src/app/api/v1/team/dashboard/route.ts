import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { computeTeamDashboard, computeStateOfDementiaCare } from "@/lib/community";
import { assertOpsAccess } from "@/lib/ops-console/access";

function authFromReq(req: NextRequest): string | null {
  return (
    req.headers.get("authorization")?.replace(/^Bearer\s+/i, "") ??
    req.nextUrl.searchParams.get("key") ??
    req.nextUrl.searchParams.get("ops_key") ??
    null
  );
}

/** GET /api/v1/team/dashboard — Mission Control overview */
export async function GET(req: NextRequest) {
  const key = authFromReq(req);

  const dashboard = computeTeamDashboard();

  const response: Record<string, unknown> = {
    ...dashboard,
    identity: "SolenOS Mission Control",
    boundary: "Internal operations dashboard. Not visible to caregivers.",
  };

  if (key) {
    response.dashboard = dashboard;
  }

  if (key && req.nextUrl.searchParams.get("report") === "true") {
    response.state_report = computeStateOfDementiaCare();
  }

  return NextResponse.json(response);
}
