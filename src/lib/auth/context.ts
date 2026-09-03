import type { AuthTokenPayload } from "./jwt";
import { extractBearerToken, verifyToken } from "./jwt";

export type AuthResult =
  | { authenticated: true; context: AuthContext }
  | { authenticated: false; error: string; status: 401 | 403 };

export interface AuthContext {
  userId: string;
  caregiverId: string;
  careRecipientIds: string[];
  role: "caregiver" | "admin";
  token: string;
}

export async function authenticateRequest(request: Request): Promise<AuthResult> {
  const token = extractBearerToken(request);
  if (!token) {
    return { authenticated: false, error: "Missing or malformed Authorization header", status: 401 };
  }

  try {
    const payload: AuthTokenPayload = await verifyToken(token);
    return {
      authenticated: true,
      context: {
        userId: payload.sub,
        caregiverId: payload.caregiver_id,
        careRecipientIds: payload.care_recipient_ids,
        role: payload.role,
        token,
      },
    };
  } catch {
    return { authenticated: false, error: "Invalid or expired token", status: 401 };
  }
}

export function authorizeCareRecipientAccess(
  auth: AuthContext,
  targetCareRecipientId: string,
): boolean {
  if (auth.role === "admin") return true;
  return auth.careRecipientIds.includes(targetCareRecipientId);
}
