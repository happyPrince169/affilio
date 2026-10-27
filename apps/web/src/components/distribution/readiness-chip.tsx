import type { MediaReadiness } from "@affilio/shared-types";
import { CircleCheck, CircleDashed, TriangleAlert } from "lucide-react";

const LABEL: Record<MediaReadiness, string | null> = {
  export_ready: "publish-ready export",
  final_ready: "final publish-ready",
  export_stale: "export outdated",
  needs_export: "needs export",
  not_final: null,
};

/** Publish-readiness of the video from indexed facts (E2). The engine re-checks the file. */
export function ReadinessChip({
  readiness,
  detail,
}: {
  readiness: MediaReadiness;
  detail: string | null;
}) {
  const label = LABEL[readiness];
  if (!label) return null;
  const ok = readiness === "export_ready" || readiness === "final_ready";
  const Icon = ok ? CircleCheck : readiness === "export_stale" ? TriangleAlert : CircleDashed;
  return (
    <span
      title={detail ?? undefined}
      className={`inline-flex items-center gap-1 text-[11px] ${
        ok ? "text-success" : readiness === "export_stale" ? "text-destructive" : "text-warning"
      }`}
    >
      <Icon className="size-3" /> {label}
    </span>
  );
}
