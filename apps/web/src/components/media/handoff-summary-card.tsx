"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileCheck, LoaderCircle, RefreshCw } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { createScan, mediaQueries } from "@/lib/api/media";

/** Final-video handoff status: counts per review state and a scan trigger for the finals root. */
export function HandoffSummaryCard() {
  const stats = useQuery(mediaQueries.stats());
  const scanStatus = useQuery(mediaQueries.scanStatus());
  const queryClient = useQueryClient();
  const finals = scanStatus.data?.roots.find((r) => r.key === "finals");
  const scan = useMutation({
    mutationFn: () => createScan({ root_key: "finals" }),
    onSuccess: () => {
      toast.success("Finals scan queued");
      void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
    },
    onError: (error) =>
      toast.error("Could not start finals scan", {
        description:
          error instanceof ApiError && error.code === "scan_already_active"
            ? "A scan is already running."
            : error.message,
      }),
  });
  const counts = stats.data;
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-2">
              <FileCheck className="size-4 text-brand" /> Final video handoff
            </CardTitle>
            <CardDescription>
              Finished videos uploaded by editors are indexed from the finals root (read-only),
              skipped while still uploading, and wait for a human approval before publishing.
            </CardDescription>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => scan.mutate()}
            disabled={
              !finals?.configured ||
              !finals.readable ||
              scan.isPending ||
              Boolean(scanStatus.data?.current)
            }
          >
            {scan.isPending ? <LoaderCircle className="animate-spin" /> : <RefreshCw />} Scan finals
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {!finals?.configured ? (
          <p className="text-sm text-muted-foreground">
            No finals root configured. Set{" "}
            <code className="font-mono text-xs">MEDIA_FINALS_PATH</code> to the folder editors
            upload finished videos to.
          </p>
        ) : counts ? (
          <div className="flex flex-wrap gap-3 text-sm">
            <Link
              href="/content?asset_kind=final&handoff_status=pending_review"
              className="rounded-md border px-3 py-1.5 hover:border-brand/50"
            >
              <span className="font-mono font-semibold">{counts.finals_pending_review}</span>{" "}
              pending review
            </Link>
            <Link
              href="/content?asset_kind=final&handoff_status=approved"
              className="rounded-md border px-3 py-1.5 hover:border-brand/50"
            >
              <span className="font-mono font-semibold text-success">{counts.finals_approved}</span>{" "}
              approved
            </Link>
            <Link
              href="/content?asset_kind=final&handoff_status=rejected"
              className="rounded-md border px-3 py-1.5 hover:border-brand/50"
            >
              <span className="font-mono font-semibold">{counts.finals_rejected}</span> rejected
            </Link>
            <span className="rounded-md border px-3 py-1.5 text-muted-foreground">
              <span className="font-mono font-semibold text-foreground">{counts.finals}</span>{" "}
              finals ·{" "}
              <span className="font-mono font-semibold text-foreground">{counts.footage}</span>{" "}
              footage
            </span>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
