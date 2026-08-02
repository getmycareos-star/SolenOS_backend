import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { z } from "zod";
import {
  getDefaultSettings,
  mergeWithDefaultSettings,
} from "@/lib/settings-governance";
import type { PrivacyControl } from "@/lib/settings-governance";
import { getTelemetryStore } from "@/lib/telemetry-persistence/server";

const QuerySchema = z.object({
  telemetry_user_id: z.string().uuid(),
});

const PrivacyUpdateSchema = z.object({
  telemetry_user_id: z.string().uuid(),
  privacyControl: z
    .object({
      exportEnabled: z.boolean().optional(),
      deleteAccountEnabled: z.boolean().optional(),
      disableInferenceEngine: z.boolean().optional(),
      disableBehaviorSignals: z.boolean().optional(),
      allowBehaviorInference: z.boolean().optional(),
    })
    .refine(
      (v) =>
        v.exportEnabled !== undefined ||
        v.deleteAccountEnabled !== undefined ||
        v.disableInferenceEngine !== undefined ||
        v.disableBehaviorSignals !== undefined ||
        v.allowBehaviorInference !== undefined,
      { message: "At least one privacy field is required" },
    ),
});

/** GET /api/user/privacy — load privacy controls for a telemetry user. */
export async function GET(req: NextRequest) {
  const parsed = QuerySchema.safeParse({
    telemetry_user_id: req.nextUrl.searchParams.get("telemetry_user_id"),
  });
  if (!parsed.success) {
    return NextResponse.json(
      { error: "telemetry_user_id query parameter required (uuid)" },
      { status: 400 },
    );
  }

  const store = await getTelemetryStore();
  await store.ensureUser(parsed.data.telemetry_user_id);
  const settings = await store.getUserGovernanceSettings(parsed.data.telemetry_user_id);
  const defaults = getDefaultSettings();
  const privacy = settings?.privacyControl ?? defaults.privacyControl;

  return NextResponse.json({ privacyControl: privacy });
}

/** PUT /api/user/privacy — persist privacy controls. */
export async function PUT(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const parsed = PrivacyUpdateSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid privacy settings", details: parsed.error.flatten() },
      { status: 400 },
    );
  }

  const store = await getTelemetryStore();
  await store.ensureUser(parsed.data.telemetry_user_id);
  const existing = await store.getUserGovernanceSettings(parsed.data.telemetry_user_id);
  const current = existing ?? getDefaultSettings();
  const merged: PrivacyControl = {
    ...current.privacyControl,
    ...parsed.data.privacyControl,
  };

  const updated = mergeWithDefaultSettings({
    ...current,
    privacyControl: merged,
  });

  await store.updateUserGovernanceSettings(parsed.data.telemetry_user_id, updated);

  return NextResponse.json({ privacyControl: merged });
}

