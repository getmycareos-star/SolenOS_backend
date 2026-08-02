import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { z } from "zod";
import { deleteUserAccount } from "@/lib/user-account";

const DeleteAccountSchema = z.object({
  user_id: z.string().uuid(),
  care_key: z.string().optional().nullable(),
  confirm: z.literal("DELETE").optional(),
});

/**
 * DELETE /api/user/account — permanently delete a user account and all associated data.
 * Requires `confirm: "DELETE"` to prevent accidental deletion.
 */
export async function DELETE(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const parsed = DeleteAccountSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "user_id (uuid) is required" },
      { status: 400 },
    );
  }

  if (parsed.data.confirm !== "DELETE") {
    return NextResponse.json(
      { error: 'Confirm account deletion by sending confirm: "DELETE"' },
      { status: 400 },
    );
  }

  try {
    await deleteUserAccount({
      user_id: parsed.data.user_id,
      care_key: parsed.data.care_key ?? null,
    });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Account deletion failed" },
      { status: 500 },
    );
  }

  return NextResponse.json({ ok: true });
}

