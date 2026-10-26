"use client";

import type { ProductionJobSummary, ProductionStatus } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays, Film, Package, Plus, Search, TriangleAlert, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { JobFormDialog } from "@/components/production/job-form-dialog";
import { EditingStatusBadge, PRODUCTION_LABEL } from "@/components/production/production-badges";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { productionQueries } from "@/lib/api/production";

const COLUMNS: ProductionStatus[] = ["planned", "scheduled", "filming", "filmed"];

function JobCard({ job }: { job: ProductionJobSummary }) {
  return (
    <Link
      href={`/production/${job.id}`}
      className="block space-y-2 rounded-lg border bg-card p-3 text-sm shadow-xs transition-colors outline-none hover:border-brand/50 focus-visible:ring-2"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-[11px] text-muted-foreground">{job.job_number}</span>
        <EditingStatusBadge status={job.editing_status} />
      </div>
      <p className="font-medium">{job.title}</p>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        {job.planned_filming_date ? (
          <span className="inline-flex items-center gap-1">
            <CalendarDays className="size-3" /> {job.planned_filming_date}
          </span>
        ) : null}
        {job.deadline ? <span>due {job.deadline}</span> : null}
        <span className="inline-flex items-center gap-1">
          <Package className="size-3" /> {job.product_count}
        </span>
        <span className="inline-flex items-center gap-1">
          <Film className="size-3" /> {job.media_count}
          {job.final_count ? ` (${job.final_count} final)` : ""}
        </span>
      </div>
      {job.staff_labels.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {job.staff_labels.map((label) => (
            <Badge key={label} variant="secondary" className="gap-1 text-[10px]">
              <Users className="size-3" /> {label}
            </Badge>
          ))}
        </div>
      ) : null}
    </Link>
  );
}

export function ProductionBoard() {
  const [q, setQ] = useState("");
  const [showCancelled, setShowCancelled] = useState(false);
  const jobs = useQuery(
    productionQueries.jobs({ q: q || undefined, page_size: 100, sort: "planned" }),
  );
  const stats = useQuery(productionQueries.stats());

  return (
    <div className="space-y-6">
      <PageHeader
        title="Production"
        description="Filming and editing jobs for real actors and staff. Footage and finished videos stay on the NAS; AFFILIO only tracks the work and optional links to products and videos."
        actions={
          <JobFormDialog
            trigger={
              <Button>
                <Plus /> New job
              </Button>
            }
          />
        }
      />
      {stats.data ? (
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary" className="font-mono">
            {stats.data.upcoming} filming in 7 days
          </Badge>
          <Badge variant={stats.data.overdue ? "destructive" : "outline"} className="font-mono">
            {stats.data.overdue} overdue
          </Badge>
          <Badge variant="outline" className="font-mono">
            {stats.data.by_editing_status.in_progress + stats.data.by_editing_status.review} in edit
          </Badge>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative max-w-sm flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search jobs…"
            className="pl-8"
            aria-label="Search jobs"
          />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={showCancelled} onCheckedChange={(v) => setShowCancelled(v === true)} />{" "}
          Show cancelled
        </label>
      </div>
      {jobs.isPending ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {COLUMNS.map((c) => (
            <Skeleton key={c} className="h-64 rounded-xl" />
          ))}
        </div>
      ) : jobs.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load jobs</AlertTitle>
          <AlertDescription>{jobs.error.message}</AlertDescription>
        </Alert>
      ) : jobs.data.total === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-16 text-center">
          <h2 className="text-lg font-semibold">No production jobs yet</h2>
          <p className="max-w-md text-sm text-muted-foreground">
            Create a job for the next shoot. Attach purchased products and, once footage or finals
            are indexed, the videos.
          </p>
          <JobFormDialog
            trigger={
              <Button size="sm">
                <Plus /> New job
              </Button>
            }
          />
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {[...COLUMNS, ...(showCancelled ? (["cancelled"] as ProductionStatus[]) : [])].map(
            (column) => {
              const items = jobs.data.items.filter((j) => j.status === column);
              return (
                <section
                  key={column}
                  className="space-y-2 rounded-xl bg-muted/40 p-3"
                  aria-label={PRODUCTION_LABEL[column]}
                >
                  <div className="flex items-center justify-between px-1">
                    <h2 className="text-sm font-semibold">{PRODUCTION_LABEL[column]}</h2>
                    <span className="font-mono text-xs text-muted-foreground">{items.length}</span>
                  </div>
                  {items.length === 0 ? (
                    <p className="rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">
                      Nothing here
                    </p>
                  ) : (
                    items.map((job) => <JobCard key={job.id} job={job} />)
                  )}
                </section>
              );
            },
          )}
        </div>
      )}
    </div>
  );
}
