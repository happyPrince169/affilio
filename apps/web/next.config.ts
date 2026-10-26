import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  cacheComponents: true,
  partialPrefetching: true,
  // Self-contained server for the Docker image (see apps/web/Dockerfile).
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../../"),
  // Workspace packages are shipped as TypeScript source and compiled by Next.
  transpilePackages: ["@affilio/shared-types"],
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
