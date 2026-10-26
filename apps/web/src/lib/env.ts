import { z } from "zod";

/**
 * Public (browser-safe) environment configuration.
 * Only `NEXT_PUBLIC_*` variables may be referenced here; they are inlined at build time.
 *
 * `NEXT_PUBLIC_API_URL`:
 *  - absolute URL (dev default `http://localhost:8000`): the browser calls the API directly (CORS).
 *  - empty string (production image default): same-origin; requests go through the Next.js
 *    route handler at `/api/[...path]`, which forwards to `API_INTERNAL_URL` server-side.
 */
const publicEnvSchema = z.object({
  NEXT_PUBLIC_API_URL: z
    .union([z.literal(""), z.string().url()])
    .default("http://localhost:8000")
    .transform((value) => value.replace(/\/+$/, "")),
});

export const env = publicEnvSchema.parse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
});
