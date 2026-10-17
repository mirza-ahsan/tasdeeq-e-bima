import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

// The browser talks only to this origin: API calls go through app/api/[...path], and
// next/font self-hosts the typefaces, so nothing else needs to be allowed. Next.js
// injects inline hydration scripts, which is why script-src keeps 'unsafe-inline' (a
// nonce would force every page to render dynamically for a page that is static).
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  // Required by frontend/Dockerfile: emits a self-contained server bundle.
  output: "standalone",
  poweredByHeader: false,
  // `next dev` serves localhost; without this, opening 127.0.0.1 gives a page that never hydrates.
  allowedDevOrigins: ["127.0.0.1"],
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
