import { NextResponse } from "next/server";
import { verifyPassword } from "@/lib/auth/password";
import { findCaregiverByEmail, updateLastLogin } from "@/lib/auth/store";
import { signToken } from "@/lib/auth/jwt";

export const runtime = "nodejs";

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
    const password = typeof body.password === "string" ? body.password : "";

    if (!email || !password) {
      return NextResponse.json(
        { error: "missing_fields", note: "Email and password are required." },
        { status: 400 },
      );
    }

    const caregiver = await findCaregiverByEmail(email);
    if (!caregiver) {
      return NextResponse.json(
        { error: "invalid_credentials", note: "Invalid email or password." },
        { status: 401 },
      );
    }

    const valid = await verifyPassword(password, caregiver.password_hash);
    if (!valid) {
      return NextResponse.json(
        { error: "invalid_credentials", note: "Invalid email or password." },
        { status: 401 },
      );
    }

    const authCtx = { userId: caregiver.id, role: "authenticated" };
    await updateLastLogin(caregiver.id, authCtx);

    const token = await signToken({
      sub: caregiver.id,
      caregiver_id: caregiver.id,
      care_recipient_ids: caregiver.care_recipient_ids,
      role: caregiver.role as "caregiver" | "admin",
    });

    return NextResponse.json({
      token,
      caregiver: {
        id: caregiver.id,
        email: caregiver.email,
        display_name: caregiver.display_name,
        care_recipient_ids: caregiver.care_recipient_ids,
        role: caregiver.role,
      },
    });
  } catch (error) {
    console.error("[api/auth/login] POST failed:", error);
    return NextResponse.json(
      { error: "login_failed", note: "An unexpected error occurred during login." },
      { status: 500 },
    );
  }
}
