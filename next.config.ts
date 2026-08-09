import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  typescript: {
    ignoreBuildErrors: true,
  },
  webpack: (config, { isServer }) => {
    // Living Care Record persistence uses Node fs on the server only.
    // Client imports of shared spine modules must not crash on node:fs / fs.
    if (!isServer) {
      config.resolve = config.resolve ?? {};
      config.resolve.fallback = {
        ...config.resolve.fallback,
        fs: false,
        path: false,
      };
    }
    return config;
  },
  /**
   * Reliable CORS for the Netlify frontend.
   *
   * NOTE: `headers()` is applied at the Next.js server level and works in
   * `output: "standalone"` mode on Railway (unlike middleware.ts which can be
   * flaky/inconsistent for OPTIONS preflight in standalone builds). This is the
   * authoritative defense-in-depth layer for cross-origin browser requests.
   *
   * The Netlify frontend now proxies /api/* same-origin (netlify.toml), so the
   * browser normally never hits this endpoint cross-origin. But keeping CORS
   * correct here protects direct-call clients and local dev.
   */
  async headers() {
    return [
      {
        source: "/api/:path*",
        headers: [
          { key: "Access-Control-Allow-Credentials", value: "true" },
          {
            key: "Access-Control-Allow-Origin",
            value: "*",
          },
          {
            key: "Access-Control-Allow-Methods",
            value: "GET, POST, PUT, DELETE, OPTIONS",
          },
          {
            key: "Access-Control-Allow-Headers",
            value: "Content-Type, Authorization, X-Requested-With",
          },
          { key: "Access-Control-Max-Age", value: "86400" },
          { key: "Vary", value: "Origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
