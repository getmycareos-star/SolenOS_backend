import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Production CORS handling for the Netlify frontend.
 *
 * IMPORTANT: This file MUST live at src/middleware.ts (or ./middleware.ts only
 * when the project does NOT use a src/ directory). SolenOS uses src/app, so the
 * previous root-level middleware.ts was silently ignored by Next.js — the built
 * middleware-manifest.json contained "middleware":{} and production never sent
 * CORS headers. Do NOT move this file back to the repo root.
 */

const ALLOWED_ORIGINS = new Set([
  "https://solenosai.netlify.app",
  "http://localhost:3000",
  "http://localhost:8888",
]);

const NETLIFY_SUFFIX = ".netlify.app";

const ALLOWED_METHODS = "GET, POST, PUT, DELETE, OPTIONS";
const ALLOWED_HEADERS =
  "Content-Type, Authorization, X-Requested-With";

function isAllowedOrigin(origin: string | null): boolean {
  if (!origin) return false;
  if (ALLOWED_ORIGINS.has(origin)) return true;
  // Allow any Netlify deploy/preview domain (e.g. <site>.netlify.app).
  if (origin.startsWith("https://") && origin.endsWith(NETLIFY_SUFFIX)) return true;
  return false;
}

function applyCorsHeaders(response: NextResponse, origin: string | null) {
  const allowed = isAllowedOrigin(origin) ? origin : null;
  if (allowed) {
    response.headers.set("Access-Control-Allow-Origin", allowed);
    response.headers.set("Vary", "Origin");
    response.headers.set("Access-Control-Allow-Methods", ALLOWED_METHODS);
    response.headers.set("Access-Control-Allow-Headers", ALLOWED_HEADERS);
    response.headers.set("Access-Control-Max-Age", "86400");
  }
  return response;
}

export function middleware(request: NextRequest) {
  const origin = request.headers.get("origin");

  // Handle browser CORS preflight before anything else.
  if (request.method === "OPTIONS") {
    const preflight = new NextResponse(null, { status: 200 });
    return applyCorsHeaders(preflight, origin);
  }

  const response = NextResponse.next();
  return applyCorsHeaders(response, origin);
}

export const config = {
  matcher: "/api/:path*",
};

