import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Only the container image asks for the self-contained server bundle. Local
  // `next build`/`next start` keep their normal output because `next start`
  // does not support standalone mode.
  ...(process.env.NEXT_STANDALONE === "1" ? { output: "standalone" as const } : {}),
  // Allows the E2E suite to run an additional configuration instance from a
  // separate production build without clobbering the development build.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};

export default nextConfig;
