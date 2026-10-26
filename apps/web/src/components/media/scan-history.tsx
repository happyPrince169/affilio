"use client";

import type { ScanJob } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ScanStatusBadge } from "@/components/media/media-badges";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { mediaQueries } from "@/lib/api/media";
import { formatDateTime, formatElapsed } from "@/lib/format";

function ErrorList({ job }: { job: ScanJob }) {
  const [open, setOpen] = useState(false);
  if (job.errors.length === 0) return <span className="text-muted-foreground">0</span>;
  return (
    <div className="space-y-1">
      <Button variant="link" size="xs" className="h-auto p-0" onClick={() => setOpen((v) => !v)}>
        {job.errors.length} {open ? "▲" : "▼"}
      </Button>
      {open ? (
        <ul className="max-h-48 space-y-1 overflow-auto rounded-md bg-muted p-2 font-mono text-[11px]">
          {job.errors.map((entry, index) => (
            <li key={index} className="truncate" title={entry.message}>
              <span className="text-destructive">{entry.kind}</span> {entry.path}: {entry.message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function ScanHistory() {
  const history = useQuery(mediaQueries.scanHistory(10));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Scan history</CardTitle>
        <CardDescription>The ten most recent scans, newest first.</CardDescription>
      </CardHeader>
      <CardContent>
        {history.isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
          </div>
        ) : history.isError ? (
          <p className="text-sm text-destructive">{history.error.message}</p>
        ) : history.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No scans recorded yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Status</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead className="text-right">Seen</TableHead>
                  <TableHead className="text-right">Added</TableHead>
                  <TableHead className="text-right">Updated</TableHead>
                  <TableHead className="text-right">Missing</TableHead>
                  <TableHead>Errors</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.data.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell>
                      <ScanStatusBadge status={job.status} />
                      {job.full_rescan ? (
                        <span className="ml-1 text-[10px] text-muted-foreground">full</span>
                      ) : null}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-muted-foreground">
                      {formatDateTime(job.started_at ?? job.created_at)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {job.started_at ? formatElapsed(job.started_at, job.finished_at) : "–"}
                    </TableCell>
                    <TableCell className="capitalize">{job.trigger}</TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {job.stats.files_seen ?? "–"}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {job.stats.added ?? "–"}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {job.stats.updated ?? "–"}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {job.stats.missing ?? "–"}
                    </TableCell>
                    <TableCell>
                      {job.error ? (
                        <span className="text-xs text-destructive" title={job.error}>
                          {job.error.slice(0, 60)}
                        </span>
                      ) : (
                        <ErrorList job={job} />
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
