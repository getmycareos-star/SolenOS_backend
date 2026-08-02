import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { z } from "zod";
import { changeUserPassword } from "@/lib/identity-continuity";

const PasswordChangeSchema = z.object({
  user_id: z.string().uuid(),
  current_password: z.string().min(1),
  new_password: z.string().min(8),
});

/** POST /api/user/password — change a persistent user's password. */
export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const parsed = PasswordChangeSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      {
        error: "user_id, current_password, and new_password (min 8 chars) required",
      },
      { status: 400 },
    );
  }

  const changed = await changeUserPassword({
    user_id: parsed.data.user_id,
    current_password: parsed.data.current_password,
    new_password: parsed.data.new_password,
  });

  if (!changed) {
    return NextResponse.json(
      { error: "No account for this user, or current password is incorrect" },
      { status: 401 },
    );
  }

  return NextResponse.json({ ok: true });
}

