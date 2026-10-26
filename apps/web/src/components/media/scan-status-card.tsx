"use client";

import type { MediaRootStatus, ScanJob } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { ChevronDown, LoaderCircle, RefreshCw, ScanSearch } from "lucide-react";
import { toast } from "sonner";

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
import { createScan, mediaQueries } from "@/lib/api/media";
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
  ["errors", "errors"],
];

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

export function ScanStatusCard() {
  const queryClient = useQueryClient();
  const status = useQuery(mediaQueries.scanStatus());
  const scan = useMutation({
    mutationFn: createScan,
    onSuccess: () => {
      toast.success("Scan queued", { description: "The worker will pick it up shortly." });
      void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
    },
    onError: (error) => {
      const message =
        error instanceof ApiError && error.code === "scan_already_active"
          ? "A scan is already queued or running."
          : error instanceof ApiError && error.code === "forbidden"
            ? "Scans require the admin role. Ask an admin, or trigger it from the CLI with the service token."
            : error.message;
      toast.error("Could not start scan", { description: message });
    },
  });

  const originals = status.data?.roots.find((root) => root.key === "originals");
  const canScan = Boolean(originals?.configured && originals.readable) && !status.data?.current;
  const current = status.data?.current ?? null;
  const last = status.data?.last_completed ?? null;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-2">
              <ScanSearch className="size-4 text-brand" />
              Library scan
            </CardTitle>
            <CardDescription>
              Incremental indexing of the originals root. Nothing under originals or proxies is ever
              written.
            </CardDescription>
          </div>
          <div className="flex items-center gap-1">
            <Button size="sm" onClick={() => scan.mutate({})} disabled={!canScan || scan.isPending}>
              {scan.isPending || current ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <RefreshCw />
              )}
              {current ? "Scanning…" : "Scan now"}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="icon-sm" variant="outline" aria-label="More scan options">
                  <ChevronDown />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  disabled={!canScan || scan.isPending}
                  onClick={() => scan.mutate({ full_rescan: true })}
                >
                  Full rescan (re-probe every file)
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => void status.refetch()}>
                  Refresh status
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
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
            {current ? (
              <div className="space-y-1.5 rounded-lg border border-brand/30 bg-accent/40 p-3">
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
            ) : null}
            {last ? (
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
            ) : !current ? (
              <p className="text-sm text-muted-foreground">No scan has run yet.</p>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
