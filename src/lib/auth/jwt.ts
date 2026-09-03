import { SignJWT, jwtVerify } from "jose";

const JWT_SECRET = process.env.JWT_SECRET;
const JWT_ISSUER = process.env.JWT_ISSUER ?? "solenos";
const JWT_AUDIENCE = process.env.JWT_AUDIENCE ?? "solenos-api";
const JWT_EXPIRATION_SECONDS = parseInt(process.env.JWT_EXPIRATION_SECONDS ?? "86400", 10);

function getSecret(): Uint8Array {
  if (!JWT_SECRET) {
    throw new Error("JWT_SECRET environment variable is not set");
  }
  return new TextEncoder().encode(JWT_SECRET);
}

export interface AuthTokenPayload {
  sub: string;
  caregiver_id: string;
  care_recipient_ids: string[];
  role: "caregiver" | "admin";
  iat?: number;
  exp?: number;
  iss?: string;
  aud?: string | string[];
}

export async function signToken(payload: Omit<AuthTokenPayload, "iat" | "exp" | "iss" | "aud">): Promise<string> {
  const secret = getSecret();
  const now = Math.floor(Date.now() / 1000);

  return new SignJWT({
    caregiver_id: payload.caregiver_id,
    care_recipient_ids: payload.care_recipient_ids,
    role: payload.role,
  })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setSubject(payload.sub)
    .setIssuedAt(now)
    .setExpirationTime(now + JWT_EXPIRATION_SECONDS)
    .setIssuer(JWT_ISSUER)
    .setAudience(JWT_AUDIENCE)
    .sign(secret);
}

export async function verifyToken(token: string): Promise<AuthTokenPayload> {
  const secret = getSecret();
  const { payload } = await jwtVerify(token, secret, {
    issuer: JWT_ISSUER,
    audience: JWT_AUDIENCE,
  });

  return {
    sub: payload.sub as string,
    caregiver_id: payload.caregiver_id as string,
    care_recipient_ids: (payload.care_recipient_ids as string[]) ?? [],
    role: (payload.role as "caregiver" | "admin") ?? "caregiver",
    iat: payload.iat,
    exp: payload.exp,
    iss: payload.iss,
    aud: payload.aud,
  };
}

export function extractBearerToken(request: Request): string | null {
  const header = request.headers.get("authorization");
  if (!header) return null;
  const match = header.match(/^Bearer\s+(.+)$/i);
  return match ? match[1] : null;
}
