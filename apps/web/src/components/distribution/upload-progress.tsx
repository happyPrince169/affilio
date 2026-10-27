"use client";

import type {
  EngineStatus,
  PublicationDetail,
  PublicationSummary,
  UploadPhase,
  UploadSummary,
} from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import {
  Activity,
  FlaskConical,
  LoaderCircle,
  RefreshCw,
  ServerOff,
  TriangleAlert,
} from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { publishingQueries, reconcileWithProvider } from "@/lib/api/publishing";
import { formatBytes, formatInZone, formatRelative } from "@/lib/format";

export const PHASE_LABEL: Record<UploadPhase, string> = {
  pending: "Queued",
  validating: "Validating media",
  session_starting: "Starting upload session",
  uploading: "Uploading",
  upload_complete: "Upload complete",
  processing: "Processing",
  finalizing: "Publish requested",
  verifying: "Verifying publication",
  retry_wait: "Waiting to retry",
  published: "Published",
  failed: "Failed",
  needs_reconciliation: "Needs reconciliation",
  abandoned: "Abandoned",
};

const PHASE_CLASS: Record<UploadPhase, string> = {
  pending: "text-muted-foreground",
  validating: "border-brand/50 text-brand",
  session_starting: "border-brand/50 text-brand",
  uploading: "border-brand bg-brand/10 text-brand",
  upload_complete: "border-brand/50 text-brand",
  processing: "border-warning bg-warning/10 text-warning",
  finalizing: "border-warning bg-warning/10 text-warning",
  verifying: "border-warning bg-warning/10 text-warning",
  retry_wait: "border-warning/60 text-warning",
  published: "border-success/50 bg-success/10 text-success",
  failed: "border-destructive/50 text-destructive",
  needs_reconciliation: "border-destructive bg-destructive/10 text-destructive",
  abandoned: "text-muted-foreground line-through",
};

export const ELIGIBILITY_LABEL: Record<string, string> = {
  account_not_connected: "Page not connected: assisted publishing",
  platform_not_supported: "Platform has no API publishing",
  mock_account_in_live_mode: "Mock Page cannot publish in live mode",
  real_account_in_mock_mode: "Real Page cannot publish in mock mode",
  live_publishing_disabled: "Live publishing is disabled (activation pending)",
  meta_not_configured: "Meta app not configured",
};

export function MockPublicationBadge({ className }: { className?: string }) {
  return (
    <Badge className={cn("gap-1 bg-amber-500 text-black hover:bg-amber-500", className)}>
      <FlaskConical className="size-3" /> Simulated (mock)
    </Badge>
  );
}

export function UploadPhaseBadge({ phase, className }: { phase: UploadPhase; className?: string }) {
  return (
    <Badge variant="outline" className={cn("whitespace-nowrap", PHASE_CLASS[phase], className)}>
      {PHASE_LABEL[phase]}
    </Badge>
  );
}

/** One-line engine status for queue rows. */
export function UploadInline({ item }: { item: PublicationSummary }) {
  const upload = item.upload;
  if (!upload) {
    if (item.publish_mode === "assisted" && item.api_eligibility_reason)
      return (
        <span className="text-[11px] text-muted-foreground">
          {ELIGIBILITY_LABEL[item.api_eligibility_reason] ?? item.api_eligibility_reason}
        </span>
      );
    return null;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-[11px]">
      <UploadPhaseBadge phase={upload.phase} className="h-5 px-1.5 text-[10px]" />
      {upload.is_mock ? <MockPublicationBadge className="h-5 px-1.5 text-[10px]" /> : null}
      {upload.phase === "uploading" && upload.progress_percent !== null ? (
        <span className="text-muted-foreground">{upload.progress_percent}%</span>
      ) : null}
      {upload.retry_count > 0 ? (
        <span className="text-warning">
          retry {upload.retry_count}/{upload.max_retries}
        </span>
      ) : null}
      {upload.last_error_code && upload.phase !== "published" ? (
        <span className="text-destructive">{upload.last_error_code}</span>
      ) : null}
    </span>
  );
}

function ProgressBar({ percent }: { percent: number | null }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
      <div
        className="h-full rounded-full bg-brand transition-[width]"
        style={{ width: `${percent ?? 0}%` }}
      />
    </div>
  );
}

/** Full upload panel for the publication detail page. */
export function UploadPanel({ upload }: { upload: UploadSummary }) {
  const { timezone } = useAuth();
  const active = !["published", "failed", "abandoned", "needs_reconciliation"].includes(
    upload.phase,
  );
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <UploadPhaseBadge phase={upload.phase} />
        {upload.is_mock ? <MockPublicationBadge /> : null}
        {active && upload.lease_owner ? (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <LoaderCircle className="size-3 animate-spin" /> worker {upload.lease_owner}
          </span>
        ) : null}
      </div>
      {upload.bytes_total ? (
        <div className="space-y-1">
          <ProgressBar percent={upload.progress_percent} />
          <p className="text-xs text-muted-foreground">
            {formatBytes(upload.bytes_sent)} of {formatBytes(upload.bytes_total)} uploaded
            {upload.processing_status ? ` · provider: ${upload.processing_status}` : ""}
          </p>
        </div>
      ) : null}
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Publish request</dt>
        <dd className="capitalize">{upload.publish_request_status.replaceAll("_", " ")}</dd>
        <dt className="text-muted-foreground">Retries</dt>
        <dd>
          {upload.retry_count}/{upload.max_retries}
          {upload.phase === "retry_wait" ? ` · next ${formatRelative(upload.next_action_at)}` : ""}
        </dd>
        {upload.provider_video_id ? (
          <>
            <dt className="text-muted-foreground">Provider video</dt>
            <dd className="font-mono">{upload.provider_video_id}</dd>
          </>
        ) : null}
        {upload.external_post_id ? (
          <>
            <dt className="text-muted-foreground">Post id</dt>
            <dd className="font-mono">{upload.external_post_id}</dd>
          </>
        ) : null}
        <dt className="text-muted-foreground">Started</dt>
        <dd>{formatInZone(upload.started_at, timezone)}</dd>
        {upload.finish_requested_at ? (
          <>
            <dt className="text-muted-foreground">Publish requested</dt>
            <dd>{formatInZone(upload.finish_requested_at, timezone)}</dd>
          </>
        ) : null}
        {upload.finished_at ? (
          <>
            <dt className="text-muted-foreground">Finished</dt>
            <dd>{formatInZone(upload.finished_at, timezone)}</dd>
          </>
        ) : null}
      </dl>
      {upload.last_error ? (
        <p
          className={cn(
            "text-xs",
            upload.last_error_transient ? "text-warning" : "text-destructive",
          )}
        >
          <span className="font-mono">{upload.last_error_code}</span>: {upload.last_error}
        </p>
      ) : null}
    </div>
  );
}

export function CheckWithProviderButton({ job }: { job: PublicationDetail }) {
  const queryClient = useQueryClient();
  const check = useMutation({
    mutationFn: () => reconcileWithProvider(job.id, { version: job.version, note: null }),
    onSuccess: (saved) => {
      toast[
        saved.status === "published" ? "success" : saved.status === "failed" ? "warning" : "info"
      ](
        saved.status === "published"
          ? "The provider confirms the publication"
          : saved.status === "failed"
            ? "The provider shows nothing was published; retry explicitly"
            : "Still unknown; nothing was re-sent",
      );
      void queryClient.invalidateQueries({ queryKey: publishingQueries.all() });
    },
    onError: (error) => toast.error("Provider check failed", { description: error.message }),
  });
  return (
    <Button size="sm" onClick={() => check.mutate()} disabled={check.isPending}>
      {check.isPending ? <LoaderCircle className="animate-spin" /> : <RefreshCw />} Check with
      provider
    </Button>
  );
}

/** Worker health, phase counts and per-Page publishing status for the queue page. */
export function EngineStatusCard() {
  const status = useQuery(publishingQueries.engine());
  if (status.isPending) return null;
  if (status.isError) {
    return (
      <Alert variant="destructive">
        <TriangleAlert />
        <AlertTitle>Engine status unavailable</AlertTitle>
        <AlertDescription>{status.error.message}</AlertDescription>
      </Alert>
    );
  }
  const data: EngineStatus = status.data;
  const active =
    (data.phase_counts.uploading ?? 0) +
    (data.phase_counts.processing ?? 0) +
    (data.phase_counts.finalizing ?? 0) +
    (data.phase_counts.verifying ?? 0) +
    (data.phase_counts.session_starting ?? 0) +
    (data.phase_counts.validating ?? 0) +
    (data.phase_counts.pending ?? 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          Publishing engine
          {data.mode === "mock" ? (
            <Badge className="gap-1 bg-amber-500 text-black hover:bg-amber-500">
              <FlaskConical className="size-3" /> MOCK
            </Badge>
          ) : null}
          {!data.live_publishing_enabled ? (
            <Badge variant="outline">live publishing off</Badge>
          ) : null}
          {data.healthy_workers > 0 ? (
            <Badge className="text-success-foreground gap-1 bg-success">
              <Activity className="size-3" /> {data.healthy_workers} worker
              {data.healthy_workers === 1 ? "" : "s"}
            </Badge>
          ) : (
            <Badge variant="destructive" className="gap-1">
              <ServerOff className="size-3" /> no publisher running
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          {active} in flight · {data.phase_counts.retry_wait ?? 0} waiting to retry ·{" "}
          {data.phase_counts.needs_reconciliation ?? 0} need reconciliation ·{" "}
          {data.phase_counts.published ?? 0} published · {data.phase_counts.failed ?? 0} failed
          {data.mode === "mock"
            ? " · simulated by the mock provider; nothing reaches Facebook"
            : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {data.workers.length > 0 ? (
          <ul className="flex flex-wrap gap-2 text-xs">
            {data.workers.map((w) => (
              <li key={w.worker_id} className="rounded-md border px-2 py-1">
                <span className="font-mono">{w.worker_id}</span> · {w.in_flight}/{w.max_concurrency}{" "}
                in flight · seen {formatRelative(w.last_seen_at)}
                {!w.healthy ? <span className="text-destructive"> · stale</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
        {data.pages.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3 font-medium">Page</th>
                  <th className="py-1 pr-3 font-medium">API</th>
                  <th className="py-1 pr-3 font-medium">In flight</th>
                  <th className="py-1 pr-3 font-medium">Reconcile</th>
                  <th className="py-1 pr-3 font-medium">Published 24h</th>
                  <th className="py-1 pr-3 font-medium">Hold</th>
                </tr>
              </thead>
              <tbody>
                {data.pages.map((p) => (
                  <tr key={p.social_account_id} className="border-t">
                    <td className="py-1 pr-3">
                      <span className="font-medium">{p.display_name}</span>
                      {p.is_mock ? <span className="ml-1 text-amber-600">(mock)</span> : null}
                    </td>
                    <td className="py-1 pr-3">
                      {p.api_eligible ? (
                        <span className="text-success">ready</span>
                      ) : (
                        <span className="text-muted-foreground">
                          {ELIGIBILITY_LABEL[p.api_eligibility_reason ?? ""] ??
                            p.api_eligibility_reason ??
                            "assisted"}
                        </span>
                      )}
                    </td>
                    <td className="py-1 pr-3">{p.live_uploads}</td>
                    <td
                      className={cn("py-1 pr-3", p.needs_reconciliation ? "text-destructive" : "")}
                    >
                      {p.needs_reconciliation}
                    </td>
                    <td className="py-1 pr-3">
                      {p.published_last_24h}/{p.daily_quota}
                    </td>
                    <td className="py-1 pr-3">
                      {p.hold_until ? (
                        <span className="text-warning">
                          {p.hold_reason} until {formatRelative(p.hold_until)}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
