import { z } from "zod";

/**
 * Public (browser-safe) environment configuration.
 * Only `NEXT_PUBLIC_*` variables may be referenced here; they are inlined at build time.
 */
const publicEnvSchema = z.object({
  NEXT_PUBLIC_API_URL: z
    .string()
    .url()
    .default("http://localhost:8000")
    .transform((value) => value.replace(/\/+$/, "")),
});

export const env = publicEnvSchema.parse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
});
