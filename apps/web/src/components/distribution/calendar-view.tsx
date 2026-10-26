"use client";

import type { PublicationSummary } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { ChevronLeft, ChevronRight, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { PUBLICATION_LABEL } from "@/components/distribution/badges";
import { DistributionTabs } from "@/components/distribution/distribution-tabs";
import { PublicationRow } from "@/components/distribution/publication-row";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { publishingQueries } from "@/lib/api/publishing";
import { formatInZone, zonedInputToIso } from "@/lib/format";

type View = "day" | "week" | "month";

/** Calendar date (YYYY-MM-DD) of an instant in the organization timezone. */
function dayKey(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(iso));
}
function todayKey(timeZone: string): string {
  return dayKey(new Date().toISOString(), timeZone);
}
function addDays(key: string, days: number): string {
  const [y, m, d] = key.split("-").map(Number);
  const date = new Date(Date.UTC(y!, m! - 1, d! + days));
  return date.toISOString().slice(0, 10);
}
function startOfWeek(key: string): string {
  const [y, m, d] = key.split("-").map(Number);
  const dow = new Date(Date.UTC(y!, m! - 1, d!)).getUTCDay(); // 0 = Sunday
  return addDays(key, -((dow + 6) % 7)); // Monday
}
function startOfMonth(key: string): string {
  return `${key.slice(0, 8)}01`;
}
function daysInMonth(key: string): number {
  const [y, m] = key.split("-").map(Number);
  return new Date(Date.UTC(y!, m!, 0)).getUTCDate();
}
function rangeFor(view: View, anchor: string): { start: string; end: string; days: string[] } {
  if (view === "day") return { start: anchor, end: addDays(anchor, 1), days: [anchor] };
  if (view === "week") {
    const start = startOfWeek(anchor);
    return {
      start,
      end: addDays(start, 7),
      days: Array.from({ length: 7 }, (_, i) => addDays(start, i)),
    };
  }
  const first = startOfMonth(anchor);
  const gridStart = startOfWeek(first);
  const count = daysInMonth(anchor);
  const gridEnd = addDays(startOfWeek(addDays(first, count - 1)), 7);
  const days: string[] = [];
  for (let k = gridStart; k < gridEnd; k = addDays(k, 1)) days.push(k);
  return { start: gridStart, end: gridEnd, days };
}

const STATUS_DOT: Record<string, string> = {
  published: "bg-success",
  failed: "bg-destructive",
  needs_reconciliation: "bg-destructive",
  publishing: "bg-warning",
  ready_to_publish: "bg-brand",
  scheduled: "bg-brand/70",
  approved: "bg-brand/40",
};

export function CalendarView() {
  const { timezone } = useAuth();
  const [view, setView] = useState<View>("week");
  const [anchor, setAnchor] = useState(() => todayKey(timezone));
  const range = useMemo(() => rangeFor(view, anchor), [view, anchor]);
  const startIso = zonedInputToIso(`${range.start}T00:00`, timezone) ?? new Date().toISOString();
  const endIso = zonedInputToIso(`${range.end}T00:00`, timezone) ?? new Date().toISOString();
  const calendar = useQuery(publishingQueries.calendar(startIso, endIso));

  const byDay = useMemo(() => {
    const map = new Map<string, PublicationSummary[]>();
    for (const item of calendar.data?.items ?? []) {
      const instant = item.scheduled_at ?? item.published_at;
      if (!instant) continue;
      const key = dayKey(instant, timezone);
      map.set(key, [...(map.get(key) ?? []), item]);
    }
    return map;
  }, [calendar.data, timezone]);

  const step = view === "day" ? 1 : view === "week" ? 7 : daysInMonth(anchor);
  const title =
    view === "month"
      ? new Intl.DateTimeFormat(undefined, {
          month: "long",
          year: "numeric",
          timeZone: "UTC",
        }).format(new Date(`${startOfMonth(anchor)}T00:00:00Z`))
      : view === "week"
        ? `${range.start} → ${addDays(range.end, -1)}`
        : new Intl.DateTimeFormat(undefined, {
            weekday: "long",
            day: "numeric",
            month: "long",
            year: "numeric",
            timeZone: "UTC",
          }).format(new Date(`${anchor}T00:00:00Z`));
  const today = todayKey(timezone);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Calendar"
        description={`Schedules are stored in UTC and shown in ${timezone}. Nothing publishes automatically; due items appear in the queue for assisted publishing.`}
      />
      <DistributionTabs />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Previous"
            onClick={() =>
              setAnchor(addDays(view === "month" ? startOfMonth(anchor) : anchor, -step))
            }
          >
            <ChevronLeft />
          </Button>
          <Button variant="outline" size="sm" onClick={() => setAnchor(today)}>
            Today
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Next"
            onClick={() =>
              setAnchor(addDays(view === "month" ? startOfMonth(anchor) : anchor, step))
            }
          >
            <ChevronRight />
          </Button>
          <span className="ml-2 text-sm font-medium">{title}</span>
        </div>
        <Tabs value={view} onValueChange={(v) => setView(v as View)}>
          <TabsList>
            <TabsTrigger value="day">Day</TabsTrigger>
            <TabsTrigger value="week">Week</TabsTrigger>
            <TabsTrigger value="month">Month</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      {calendar.isPending ? (
        <Skeleton className="h-96 rounded-xl" />
      ) : calendar.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load calendar</AlertTitle>
          <AlertDescription>{calendar.error.message}</AlertDescription>
        </Alert>
      ) : view === "day" ? (
        <div className="grid gap-2">
          {(byDay.get(anchor) ?? []).length === 0 ? (
            <p className="rounded-xl border border-dashed px-6 py-12 text-center text-sm text-muted-foreground">
              Nothing scheduled this day.
            </p>
          ) : (
            (byDay.get(anchor) ?? []).map((item) => <PublicationRow key={item.id} item={item} />)
          )}
        </div>
      ) : view === "week" ? (
        <div className="grid gap-2 md:grid-cols-7">
          {range.days.map((day) => (
            <div
              key={day}
              className={cn("min-h-32 rounded-lg border p-2", day === today && "border-brand/60")}
            >
              <button
                type="button"
                onClick={() => {
                  setAnchor(day);
                  setView("day");
                }}
                className="mb-2 text-xs font-medium hover:underline"
              >
                {new Intl.DateTimeFormat(undefined, {
                  weekday: "short",
                  day: "numeric",
                  timeZone: "UTC",
                }).format(new Date(`${day}T00:00:00Z`))}
              </button>
              <ul className="space-y-1">
                {(byDay.get(day) ?? []).map((item) => (
                  <li key={item.id}>
                    <Link
                      href={`/distribution/publications/${item.id}`}
                      className="block rounded-md border px-1.5 py-1 text-[11px] hover:border-brand/50"
                      title={PUBLICATION_LABEL[item.status]}
                    >
                      <span className="flex items-center gap-1">
                        <span
                          className={cn(
                            "size-1.5 shrink-0 rounded-full",
                            STATUS_DOT[item.status] ?? "bg-muted-foreground",
                          )}
                        />
                        <span className="truncate font-medium">
                          {formatInZone(item.scheduled_at ?? item.published_at, timezone, {
                            hour: "2-digit",
                            minute: "2-digit",
                            year: undefined,
                            month: undefined,
                            day: undefined,
                          })}
                        </span>
                      </span>
                      <span className="block truncate">{item.account_display_name}</span>
                      <span className="block truncate text-muted-foreground">
                        {item.title || item.media_title}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-7 gap-1 text-xs">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
            <div key={d} className="px-1 py-1 text-center font-medium text-muted-foreground">
              {d}
            </div>
          ))}
          {range.days.map((day) => {
            const items = byDay.get(day) ?? [];
            const inMonth = day.slice(0, 7) === anchor.slice(0, 7);
            return (
              <button
                key={day}
                type="button"
                onClick={() => {
                  setAnchor(day);
                  setView("day");
                }}
                className={cn(
                  "min-h-20 rounded-md border p-1 text-left hover:border-brand/50",
                  !inMonth && "opacity-40",
                  day === today && "border-brand/60",
                )}
              >
                <span className="block text-[11px] font-medium">{Number(day.slice(8))}</span>
                <span className="mt-1 flex flex-wrap gap-0.5">
                  {items.slice(0, 6).map((item) => (
                    <span
                      key={item.id}
                      className={cn(
                        "size-2 rounded-full",
                        STATUS_DOT[item.status] ?? "bg-muted-foreground",
                      )}
                      title={`${item.account_display_name} · ${PUBLICATION_LABEL[item.status]}`}
                    />
                  ))}
                  {items.length > 6 ? (
                    <span className="text-[10px] text-muted-foreground">+{items.length - 6}</span>
                  ) : null}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
