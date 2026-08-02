import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { z } from "zod";
import {
  getDefaultSettings,
  mergeWithDefaultSettings,
} from "@/lib/settings-governance";
import type { NotificationControl } from "@/lib/settings-governance";
import { getTelemetryStore } from "@/lib/telemetry-persistence/server";

const QuerySchema = z.object({
  telemetry_user_id: z.string().uuid(),
});

const UrgencyFilterSchema = z.enum(["RED", "RED_ORANGE", "ALL"]);
const DigestModeSchema = z.enum(["instant", "hourly", "daily"]);

const NotificationUpdateSchema = z.object({
  telemetry_user_id: z.string().uuid(),
  notificationControl: z
    .object({
      urgencyFilter: UrgencyFilterSchema.optional(),
      quietHoursEnabled: z.boolean().optional(),
      emergencyOverride: z.boolean().optional(),
      digestMode: DigestModeSchema.optional(),
    })
    .refine(
      (v) =>
        v.urgencyFilter !== undefined ||
        v.quietHoursEnabled !== undefined ||
        v.emergencyOverride !== undefined ||
        v.digestMode !== undefined,
      { message: "At least one notification field is required" },
    ),
});

/** GET /api/user/notifications — load notification preferences for a telemetry user. */
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
  const notification = settings?.notificationControl ?? defaults.notificationControl;

  return NextResponse.json({ notificationControl: notification });
}

/** PUT /api/user/notifications — persist notification preferences. */
export async function PUT(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const parsed = NotificationUpdateSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid notification settings", details: parsed.error.flatten() },
      { status: 400 },
    );
  }

  const store = await getTelemetryStore();
  await store.ensureUser(parsed.data.telemetry_user_id);
  const existing = await store.getUserGovernanceSettings(parsed.data.telemetry_user_id);
  const current = existing ?? getDefaultSettings();
  const merged: NotificationControl = {
    ...current.notificationControl,
    ...parsed.data.notificationControl,
  };

  const updated = mergeWithDefaultSettings({
    ...current,
    notificationControl: merged,
  });

  await store.updateUserGovernanceSettings(parsed.data.telemetry_user_id, updated);

  return NextResponse.json({ notificationControl: merged });
}

