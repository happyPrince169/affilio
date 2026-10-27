"use client";

import type { MediaRootStatus, RootScanState, ScanJob } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { ChevronDown, LoaderCircle, RefreshCw, ScanSearch, Square } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { ScanStatusBadge } from "@/components/media/media-badges";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api/client";
import { cancelScan, createScan, mediaQueries } from "@/lib/api/media";
import { formatElapsed, formatRelative } from "@/lib/format";

const STAT_LABELS: Array<[string, string]> = [
  ["files_seen", "seen"],
  ["added", "added"],
  ["updated", "updated"],
  ["moved", "moved"],
  ["missing", "missing"],
  ["unsupported", "unsupported"],
  ["duplicates", "duplicates"],
  ["proxies_linked", "proxies"],
  ["settling", "settling"],
  ["errors", "errors"],
];

/** Copy per scannable root. Unknown roots (future) fall back to their key. */
const ROOT_COPY: Record<string, { title: string; description: string; action: string }> = {
  originals: {
    title: "Originals",
    description: "Camera footage; proxies are linked by file name.",
    action: "Scan now",
  },
  finals: {
    title: "Finals",
    description: "Finished edits from editors; new files enter handoff review.",
    action: "Scan finals",
  },
};

function rootCopy(key: string) {
  return ROOT_COPY[key] ?? { title: key, description: "", action: `Scan ${key}` };
}

function RootChip({ root }: { root: MediaRootStatus }) {
  const ok = root.configured && (root.access === "read_only" ? root.readable : root.writable);
  const state = !root.configured ? "not configured" : ok ? "ok" : "unavailable";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px]">
          <span
            aria-hidden
            className={cn(
              "size-1.5 rounded-full",
              !root.configured && "bg-muted-foreground/50",
              root.configured && ok && "bg-success",
              root.configured && !ok && "bg-destructive",
            )}
          />
          <span className="font-medium">{root.key}</span>
          <span className="text-muted-foreground">{state}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-64">
        <p>{root.name}</p>
        <p className="opacity-80">
          {root.access === "read_only" ? "Read-only mount" : "Read-write"} ·{" "}
          {root.configured
            ? root.exists
              ? root.readable
                ? "readable"
                : "exists but not readable"
              : "path does not exist"
            : "no path configured"}
        </p>
      </TooltipContent>
    </Tooltip>
  );
}

function ScanSummary({ job }: { job: ScanJob }) {
  const stats = job.stats ?? {};
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
      {STAT_LABELS.filter(([key]) => stats[key] !== undefined).map(([key, label]) => (
        <span key={key}>
          <span
            className={cn(
              "font-semibold text-foreground",
              key === "errors" && (stats[key] ?? 0) > 0 && "text-destructive",
            )}
          >
            {stats[key]}
          </span>{" "}
          {label}
        </span>
      ))}
    </div>
  );
}

interface RootScanRowProps {
  state: RootScanState;
  root: MediaRootStatus | undefined;
  canTrigger: boolean;
  scanPendingFor: string | null;
  cancelPending: boolean;
  onScan: (rootKey: string, fullRescan: boolean) => void;
  onCancel: (job: ScanJob) => void;
}

function RootScanRow({
  state,
  root,
  canTrigger,
  scanPendingFor,
  cancelPending,
  onScan,
  onCancel,
}: RootScanRowProps) {
  const copy = rootCopy(state.root_key);
  const available = Boolean(root?.configured && root.readable);
  const current = state.current;
  const last = state.last_completed;
  const scanPending = scanPendingFor === state.root_key;
  // One active job per root is enforced by the API (409 scan_already_active); the UI simply
  // never offers a second request while one is queued or running.
  const canScan = canTrigger && available && !current && !scanPending;

  return (
    <section
      aria-label={`${copy.title} scan`}
      className={cn("space-y-2 rounded-lg border p-3", current && "border-brand/30 bg-accent/40")}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-0.5">
          <h3 className="text-sm font-medium">{copy.title}</h3>
          <p className="text-xs text-muted-foreground">
            {copy.description}
            {!root?.configured ? " Root not configured." : null}
            {root?.configured && !root.readable ? " Root not readable right now." : null}
          </p>
        </div>
        {canTrigger ? (
          <div className="flex items-center gap-1">
            {current ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => onCancel(current)}
                disabled={cancelPending}
                aria-label={`Cancel ${copy.title.toLowerCase()} scan`}
              >
                <Square />
                Cancel
              </Button>
            ) : null}
            <Button
              size="sm"
              onClick={() => onScan(state.root_key, false)}
              disabled={!canScan}
              aria-label={copy.action}
            >
              {scanPending || current ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
              {current ? "Scanning…" : copy.action}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  size="icon-sm"
                  variant="outline"
                  aria-label={`More ${copy.title.toLowerCase()} scan options`}
                >
                  <ChevronDown />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem disabled={!canScan} onClick={() => onScan(state.root_key, true)}>
                  Full rescan (re-probe every file)
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ) : null}
      </div>
      {current ? (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-2 text-sm">
            <span className="flex items-center gap-2 font-medium">
              <ScanStatusBadge status={current.status} />
              {current.status === "running"
                ? `Running for ${formatElapsed(current.started_at, null)}`
                : `Queued ${formatRelative(current.created_at)}`}
            </span>
            <span className="text-xs text-muted-foreground">
              attempt {current.attempt}/{current.max_attempts}
            </span>
          </div>
          <ScanSummary job={current} />
        </div>
      ) : last ? (
        <div className="space-y-1.5 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <ScanStatusBadge status={last.status} />
            <span className="text-muted-foreground">
              Last scan {formatRelative(last.finished_at ?? last.created_at)} · took{" "}
              {formatElapsed(last.started_at, last.finished_at)}
            </span>
          </div>
          <ScanSummary job={last} />
          {last.error ? <p className="text-xs text-destructive">{last.error}</p> : null}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No scan has run yet.</p>
      )}
    </section>
  );
}

export function ScanStatusCard() {
  const queryClient = useQueryClient();
  const { can } = useAuth();
  const canTrigger = can("trigger_scans");
  const status = useQuery(mediaQueries.scanStatus());
  const scan = useMutation({
    mutationFn: createScan,
    onSuccess: (job) => {
      toast.success(`${rootCopy(job.root_key).title} scan queued`, {
        description: "The worker will pick it up shortly.",
      });
      void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
    },
    onError: (error) => {
      const message =
        error instanceof ApiError && error.code === "scan_already_active"
          ? "A scan of this root is already queued or running."
          : error instanceof ApiError && error.code === "forbidden"
            ? "Scans require the admin role. Ask an admin, or trigger it from the CLI with the service token."
            : error.message;
      toast.error("Could not start scan", { description: message });
      void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
    },
  });
  const cancel = useMutation({
    mutationFn: cancelScan,
    onSuccess: (job) => {
      toast.success(`${rootCopy(job.root_key).title} scan cancelled`, {
        description: "A running worker stops at its next checkpoint. Files indexed so far stay.",
      });
      void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
    },
    onError: (error) => {
      const message =
        error instanceof ApiError && error.code === "scan_not_cancellable"
          ? "This scan has already finished."
          : error instanceof ApiError && error.code === "forbidden"
            ? "Cancelling a scan requires the admin role."
            : error.message;
      toast.error("Could not cancel scan", { description: message });
      void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
    },
  });

  const rootsByKey = new Map((status.data?.roots ?? []).map((root) => [root.key, root]));
  const byRoot = status.data?.by_root ?? [];

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-2">
              <ScanSearch className="size-4 text-brand" />
              Library scans
            </CardTitle>
            <CardDescription>
              Incremental indexing of the originals and finals roots. Nothing under originals,
              proxies or finals is ever written.
            </CardDescription>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void status.refetch()}
            aria-label="Refresh scan status"
          >
            <RefreshCw />
            Refresh
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {status.isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-5 w-1/2" />
          </div>
        ) : status.isError ? (
          <p className="text-sm text-destructive">
            Scan status unavailable: {status.error.message}
          </p>
        ) : (
          <>
            <div className="flex flex-wrap gap-2">
              {status.data.roots.map((root) => (
                <RootChip key={root.key} root={root} />
              ))}
            </div>
            {!canTrigger ? (
              <p className="text-xs text-muted-foreground">
                Starting or cancelling scans requires the admin role.
              </p>
            ) : null}
            <div className="grid gap-3 md:grid-cols-2">
              {byRoot.map((state) => (
                <RootScanRow
                  key={state.root_key}
                  state={state}
                  root={rootsByKey.get(state.root_key)}
                  canTrigger={canTrigger}
                  scanPendingFor={scan.isPending ? (scan.variables?.root_key ?? null) : null}
                  cancelPending={cancel.isPending}
                  onScan={(rootKey, fullRescan) =>
                    scan.mutate({ root_key: rootKey, full_rescan: fullRescan })
                  }
                  onCancel={(job) => cancel.mutate(job.id)}
                />
              ))}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
