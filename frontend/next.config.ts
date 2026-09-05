import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required by frontend/Dockerfile: emits a self-contained server bundle.
  output: "standalone",
};

export default nextConfig;
