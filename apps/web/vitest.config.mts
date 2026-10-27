import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Component tests (M3C.1 step 2). Pure jsdom; no Next runtime, no network: `@/lib/api/client`
// is mocked per test so the real query/mutation wiring in `@/lib/api/*` is exercised.
export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
