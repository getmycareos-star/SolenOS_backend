import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { computeStateOfDementiaCare } from "@/lib/community";

/** GET /api/v1/team/state-report — State of Dementia Care report */
export async function GET(_req: NextRequest) {
  const report = computeStateOfDementiaCare();

  return NextResponse.json({
    identity: "SolenOS State of Dementia Care Report",
    boundary: "Internal operations report.",
    ...report,
  });
}
