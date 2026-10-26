"use client";

import { useQuery } from "@tanstack/react-query";
import { Send } from "lucide-react";
import Link from "next/link";

import { Card, CardContent } from "@/components/ui/card";
import { publishingQueries } from "@/lib/api/publishing";

export function DistributionStatsTile() {
  const stats = useQuery(publishingQueries.stats());
  const value = stats.data ? stats.data.by_status.scheduled.toLocaleString() : "—";
  const note = stats.data
    ? `${stats.data.due_now} due now · ${stats.data.needs_attention} need attention · ${stats.data.by_status.published} published (operator-reported)`
    : stats.isError
      ? "Distribution stats unavailable."
      : "Loading distribution stats…";
  return (
    <Link href="/distribution/queue" className="rounded-xl outline-none focus-visible:ring-2">
      <Card className="h-full gap-3 py-5 transition-colors hover:border-brand/50">
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-muted-foreground">
              Scheduled publications
            </span>
            <Send className="size-4 text-muted-foreground" />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-semibold tracking-tight">{value}</span>
            <span className="rounded-md border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
              M3A
            </span>
          </div>
          <p className="text-xs text-muted-foreground">{note}</p>
        </CardContent>
      </Card>
    </Link>
  );
}
