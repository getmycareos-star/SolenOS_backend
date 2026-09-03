import { NextResponse } from "next/server";
import { hashPassword, validatePasswordStrength } from "@/lib/auth/password";
import { createCaregiver, findCaregiverByEmail } from "@/lib/auth/store";
import { signToken } from "@/lib/auth/jwt";

export const runtime = "nodejs";

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
    const password = typeof body.password === "string" ? body.password : "";
    const displayName = typeof body.display_name === "string" ? body.display_name.trim() : "";
    const careRecipientIds = Array.isArray(body.care_recipient_ids)
      ? body.care_recipient_ids.filter((id: unknown) => typeof id === "string")
      : [];
    const authUserId = typeof body.auth_user_id === "string" ? body.auth_user_id : null;

    if (!email || !password) {
      return NextResponse.json(
        { error: "missing_fields", note: "Email and password are required." },
        { status: 400 },
      );
    }

    const passwordCheck = validatePasswordStrength(password);
    if (!passwordCheck.valid) {
      return NextResponse.json(
        { error: "weak_password", note: passwordCheck.errors.join("; ") },
        { status: 400 },
      );
    }

    if (!email.includes("@")) {
      return NextResponse.json(
        { error: "invalid_email", note: "A valid email address is required." },
        { status: 400 },
      );
    }

    if (!authUserId) {
      return NextResponse.json(
        { error: "missing_auth_user_id", note: "auth_user_id is required. Create a Supabase Auth user first." },
        { status: 400 },
      );
    }

    const existing = await findCaregiverByEmail(email);
    if (existing) {
      return NextResponse.json(
        { error: "email_taken", note: "An account with this email already exists." },
        { status: 409 },
      );
    }

    const passwordHash = await hashPassword(password);
    const caregiverId = authUserId;

    const authCtx = { userId: authUserId, role: "authenticated" };
    const caregiver = await createCaregiver({
      id: caregiverId,
      email,
      password_hash: passwordHash,
      display_name: displayName || email.split("@")[0],
      care_recipient_ids: careRecipientIds,
    }, authCtx);

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
    console.error("[api/auth/register] POST failed:", error);
    return NextResponse.json(
      { error: "registration_failed", note: "An unexpected error occurred during registration." },
      { status: 500 },
    );
  }
}
