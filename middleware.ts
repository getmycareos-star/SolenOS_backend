import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

// Allowed origins — the deployed Netlify frontend plus local dev.
const ALLOWED_ORIGINS = new Set([
  'https://solenosai.netlify.app',
  'http://localhost:3000',
  'http://localhost:8888',
]);

export function middleware(request: NextRequest) {
  const origin = request.headers.get('origin');
  const isAllowed = origin ? ALLOWED_ORIGINS.has(origin) : true; // same-origin / no origin

  const response = NextResponse.next();

  if (isAllowed) {
    response.headers.set('Access-Control-Allow-Origin', origin ?? '*');
    response.headers.set('Vary', 'Origin');
    response.headers.set('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
    response.headers.set('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  }

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: response.headers });
  }

  return response;
}

export const config = {
  matcher: '/api/:path*',
};
