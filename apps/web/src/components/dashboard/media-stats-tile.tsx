"use client";

import { useQuery } from "@tanstack/react-query";
import { Film } from "lucide-react";
import Link from "next/link";

import { Card, CardContent } from "@/components/ui/card";
import { mediaQueries } from "@/lib/api/media";

/** Live count from the media library; shows an honest dash when the API is unreachable. */
export function MediaStatsTile() {
  const stats = useQuery(mediaQueries.stats());
  const value = stats.data ? stats.data.total.toLocaleString() : "—";
  const note = stats.data
    ? stats.data.total === 0
      ? "No media indexed yet. Run a scan from the Content Library."
      : `${stats.data.active} active · ${stats.data.missing} missing · ${stats.data.thumbnails_pending} thumbnails pending`
    : stats.isError
      ? "Media stats unavailable (API offline)."
      : "Loading media stats…";

  return (
    <Link href="/content" className="rounded-xl outline-none focus-visible:ring-2">
      <Card className="h-full gap-3 py-5 transition-colors hover:border-brand/50">
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-muted-foreground">Media assets</span>
            <Film className="size-4 text-muted-foreground" />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-semibold tracking-tight">{value}</span>
            <span className="rounded-md border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
              M1
            </span>
          </div>
          <p className="text-xs text-muted-foreground">{note}</p>
        </CardContent>
      </Card>
    </Link>
  );
}
