"use client";

import type { AssetExportState, AssetExportStatus } from "@affilio/shared-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Clapperboard, LoaderCircle, Square } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { cancelExport, createExport, mediaQueries } from "@/lib/api/media";
import { formatBytes, formatRelative } from "@/lib/format";

const STATE: Record<AssetExportState, { label: string; tone: string; description: string }> = {
  not_needed: {
    label: "Not needed",
    tone: "border-success/50 text-success",
    description:
      "The final already satisfies the publishing profile; the publisher streams it directly.",
  },
  not_generated: {
    label: "Not generated",
    tone: "border-warning/50 text-warning",
    description:
      "The final does not satisfy the publishing profile; a publish-ready export is required.",
  },
  queued: {
    label: "Queued",
    tone: "border-brand/50 text-brand",
    description: "Waiting for the export worker.",
  },
  processing: {
    label: "Processing",
    tone: "border-brand/50 text-brand",
    description: "ffmpeg is rendering the export on the NAS.",
  },
  ready: {
    label: "Ready",
    tone: "border-success/50 text-success",
    description:
      "A current publish-ready export exists; the publisher streams it instead of the final.",
  },
  failed: {
    label: "Failed",
    tone: "border-destructive/50 text-destructive",
    description: "The last export attempt failed.",
  },
  blocked: {
    label: "Blocked",
    tone: "border-destructive/50 text-destructive",
    description:
      "The source carries HDR / wide-gamut colour metadata; a tone-mapping policy is required.",
  },
  stale: {
    label: "Stale",
    tone: "border-destructive/50 text-destructive",
    description:
      "The final changed after the export was rendered, or the export file is gone; it will not be used.",
  },
};

export function ExportStatusCard({
  assetId,
  status,
}: {
  assetId: string;
  status: AssetExportStatus;
}) {
  const queryClient = useQueryClient();
  const { can } = useAuth();
  const canManage = can("manage_exports");
  const meta = STATE[status.state];
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
  const create = useMutation({
    mutationFn: () => createExport(assetId),
    onSuccess: () => {
      toast.success("Export queued", { description: "The export worker renders it next." });
      invalidate();
    },
    onError: (error) => {
      const message =
        error instanceof ApiError && error.code === "export_already_active"
          ? "An export is already queued or running."
          : error instanceof ApiError && error.code === "export_not_eligible"
            ? "Only active finals with a present file can be exported."
            : error.message;
      toast.error("Could not queue export", { description: message });
      invalidate();
    },
  });
  const cancel = useMutation({
    mutationFn: (jobId: string) => cancelExport(jobId),
    onSuccess: () => {
      toast.success("Export cancelled");
      invalidate();
    },
    onError: (error) => {
      toast.error("Could not cancel export", { description: error.message });
      invalidate();
    },
  });
  const active = status.state === "queued" || status.state === "processing";
  const actionLabel =
    status.state === "ready" || status.state === "stale" ? "Re-export" : "Create export";

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-2">
              <Clapperboard className="size-4 text-brand" />
              Publish-ready export
              <Badge variant="outline" className={meta.tone} data-testid="export-state">
                {meta.label}
              </Badge>
            </CardTitle>
            <CardDescription>{meta.description}</CardDescription>
          </div>
          {canManage ? (
            <div className="flex items-center gap-1">
              {active && status.job ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => cancel.mutate(status.job!.id)}
                  disabled={cancel.isPending}
                >
                  <Square /> Cancel
                </Button>
              ) : null}
              {status.state !== "not_needed" && status.state !== "blocked" ? (
                <Button
                  size="sm"
                  onClick={() => create.mutate()}
                  disabled={!status.can_export || active || create.isPending}
                >
                  {create.isPending || active ? <LoaderCircle className="animate-spin" /> : null}
                  {actionLabel}
                </Button>
              ) : null}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">Admin role required to export.</p>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {status.detail ? (
          <p
            className={
              status.state === "failed" || status.state === "blocked" || status.state === "stale"
                ? "text-xs text-destructive"
                : "text-xs text-muted-foreground"
            }
          >
            {status.detail}
          </p>
        ) : null}
        {status.export ? (
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted-foreground">File</dt>
            <dd className="font-mono">exports/{status.export.relative_path}</dd>
            <dt className="text-muted-foreground">Encoding</dt>
            <dd>
              {status.export.video_codec ?? "?"} {status.export.pixel_format ?? ""}{" "}
              {status.export.width && status.export.height
                ? `${status.export.width}x${status.export.height}`
                : ""}
              {status.export.audio_codec ? ` · ${status.export.audio_codec}` : ""}
            </dd>
            <dt className="text-muted-foreground">Size</dt>
            <dd>{formatBytes(status.export.file_size)}</dd>
            <dt className="text-muted-foreground">Recipe</dt>
            <dd className="font-mono">{status.export.recipe_key ?? "—"}</dd>
          </dl>
        ) : null}
        {status.job ? (
          <p className="text-xs text-muted-foreground">
            Last job: <span className="capitalize">{status.job.status}</span>
            {status.job.attempt
              ? ` · attempt ${status.job.attempt}/${status.job.max_attempts}`
              : ""}
            {status.job.finished_at
              ? ` · finished ${formatRelative(status.job.finished_at)}`
              : ` · queued ${formatRelative(status.job.created_at)}`}
            {status.job.error_code ? ` · ${status.job.error_code}` : ""}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
