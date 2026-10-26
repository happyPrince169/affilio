import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  cacheComponents: true,
  partialPrefetching: true,
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
