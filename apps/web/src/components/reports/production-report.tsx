"use client";

import type { ProductionReportFilters, ProductionRow, ReportBucket } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { Download, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { analyticsQueries, productionCsvUrl } from "@/lib/api/analytics";
import { formatDateTime } from "@/lib/format";

const ALL = "__all__";
const PAGE_SIZE = 50;

const STATUS_LABEL: Record<string, string> = {
  pending_review: "Pending review",
  approved: "Approved",
  rejected: "Rejected",
};

function statusBadge(status: string) {
  switch (status) {
    case "approved":
      return <Badge>Approved</Badge>;
    case "rejected":
      return <Badge variant="destructive">Rejected</Badge>;
    default:
      return <Badge variant="secondary">Pending review</Badge>;
  }
}

function Kpi({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">{value}</CardTitle>
      </CardHeader>
      {hint ? (
        <CardContent className="pt-0 text-xs text-muted-foreground">{hint}</CardContent>
      ) : null}
    </Card>
  );
}

function BucketTable({
  title,
  rows,
  firstColumn,
}: {
  title: string;
  rows: ReportBucket[];
  firstColumn: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing in this selection.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{firstColumn}</TableHead>
                <TableHead className="text-right">Files</TableHead>
                <TableHead className="text-right">Submitted</TableHead>
                <TableHead className="text-right">Pending</TableHead>
                <TableHead className="text-right">Approved</TableHead>
                <TableHead className="text-right">Rejected</TableHead>
                <TableHead className="text-right">Corrections</TableHead>
                <TableHead className="text-right">Approved units</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((b) => (
                <TableRow key={b.key}>
                  <TableCell>
                    {b.label}
                    {b.key === "unknown" ? (
                      <Badge variant="outline" className="ml-2">
                        historical
                      </Badge>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{b.files}</TableCell>
                  <TableCell className="text-right tabular-nums">{b.submissions}</TableCell>
                  <TableCell className="text-right tabular-nums">{b.pending}</TableCell>
                  <TableCell className="text-right tabular-nums">{b.approved}</TableCell>
                  <TableCell className="text-right tabular-nums">{b.rejected}</TableCell>
                  <TableCell className="text-right tabular-nums">{b.corrected}</TableCell>
                  <TableCell className="text-right font-medium tabular-nums">
                    {b.approved_units}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function personLabel(row: ProductionRow): string {
  const p = row.attributed_editor;
  return p ? (p.display_name ?? p.email) : "Unknown";
}

export function ProductionReport() {
  const { can } = useAuth();
  const allowed = can("approve");
  const [filters, setFilters] = useState<ProductionReportFilters>({});
  const [page, setPage] = useState(1);
  const periods = useQuery({ ...analyticsQueries.productionPeriods(), enabled: allowed });
  const summary = useQuery({ ...analyticsQueries.productionSummary(filters), enabled: allowed });
  const rows = useQuery({
    ...analyticsQueries.productionRows(filters, page, PAGE_SIZE),
    enabled: allowed,
  });

  const update = (patch: ProductionReportFilters) => {
    setFilters((prev) => ({ ...prev, ...patch }));
    setPage(1);
  };

  if (!allowed) {
    return (
      <>
        <PageHeader title="Production report" />
        <Alert>
          <TriangleAlert />
          <AlertTitle>Admins only</AlertTitle>
          <AlertDescription>
            Production reporting shows every editor&apos;s work and is limited to admins.
          </AlertDescription>
        </Alert>
      </>
    );
  }

  const totals = summary.data?.totals;
  const pageCount = rows.data ? Math.max(1, Math.ceil(rows.data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <PageHeader
        title="Production report"
        description="Final videos per production owner, editor and period: submitted, reviewed and approved. Not a payroll calculation."
        actions={
          <Button asChild variant="outline">
            <a href={productionCsvUrl(filters)} download>
              <Download /> Export CSV
            </a>
          </Button>
        }
      />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div className="space-y-1.5">
          <Label htmlFor="report-period">Production period</Label>
          <Select
            value={filters.period ?? ALL}
            onValueChange={(v) => update({ period: v === ALL ? undefined : v })}
          >
            <SelectTrigger id="report-period" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All periods</SelectItem>
              {(periods.data ?? []).map((p) => (
                <SelectItem key={p.period} value={p.period}>
                  {p.period} · {p.files} files
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="report-owner">Production owner</Label>
          <Select
            value={filters.owner_id ?? ALL}
            onValueChange={(v) => update({ owner_id: v === ALL ? undefined : v })}
          >
            <SelectTrigger id="report-owner" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All owners</SelectItem>
              {(summary.data?.by_owner ?? [])
                .filter((b) => b.key !== "unknown")
                .map((b) => (
                  <SelectItem key={b.key} value={b.key}>
                    {b.label}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="report-editor">Editor</Label>
          <Select
            value={filters.unknown_editor ? "unknown" : (filters.editor_id ?? ALL)}
            onValueChange={(v) =>
              update(
                v === ALL
                  ? { editor_id: undefined, unknown_editor: undefined }
                  : v === "unknown"
                    ? { editor_id: undefined, unknown_editor: true }
                    : { editor_id: v, unknown_editor: undefined },
              )
            }
          >
            <SelectTrigger id="report-editor" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All editors</SelectItem>
              {(summary.data?.by_editor ?? []).map((b) => (
                <SelectItem key={b.key} value={b.key}>
                  {b.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="report-status">Review status</Label>
          <Select
            value={filters.status ?? ALL}
            onValueChange={(v) =>
              update({ status: v === ALL ? undefined : (v as ProductionReportFilters["status"]) })
            }
          >
            <SelectTrigger id="report-status" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All statuses</SelectItem>
              {Object.entries(STATUS_LABEL).map(([k, v]) => (
                <SelectItem key={k} value={k}>
                  {v}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="report-source">Source</Label>
          <Select
            value={filters.attribution ?? ALL}
            onValueChange={(v) =>
              update({ attribution: v === ALL ? undefined : (v as "upload" | "historical") })
            }
          >
            <SelectTrigger id="report-source" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Uploads and historical</SelectItem>
              <SelectItem value="upload">Uploads only</SelectItem>
              <SelectItem value="historical">Historical (scanned) only</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {summary.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load the report</AlertTitle>
          <AlertDescription>{summary.error.message}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" aria-busy={summary.isPending}>
        <Kpi
          label="Submitted (uploads)"
          value={totals?.submissions ?? 0}
          hint={`${totals?.historical ?? 0} historical files scanned from the NAS`}
        />
        <Kpi label="Pending review" value={totals?.pending ?? 0} />
        <Kpi
          label="Approved videos"
          value={totals?.approved ?? 0}
          hint={`${totals?.corrected ?? 0} corrected submissions`}
        />
        <Kpi label="Rejected" value={totals?.rejected ?? 0} />
        <Kpi
          label="Unique approved work units"
          value={totals?.approved_units ?? 0}
          hint="One per original video, however many corrections it took"
        />
      </div>

      {summary.data && (summary.data.unknown_editor > 0 || summary.data.missing_files > 0) ? (
        <Alert>
          <TriangleAlert />
          <AlertTitle>Data quality</AlertTitle>
          <AlertDescription>
            {summary.data.unknown_editor > 0
              ? `${summary.data.unknown_editor} file(s) were indexed from the NAS and have no uploader or editor on record. `
              : ""}
            {summary.data.missing_files > 0
              ? `${summary.data.missing_files} file(s) are currently missing from the NAS. `
              : ""}
            Periods are the month a file was first indexed, not the month it was edited.
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <BucketTable
          title="By production owner"
          rows={summary.data?.by_owner ?? []}
          firstColumn="Owner"
        />
        <BucketTable title="By editor" rows={summary.data?.by_editor ?? []} firstColumn="Editor" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Videos</CardTitle>
          <CardDescription>
            {rows.data ? `${rows.data.total} video(s) · page ${page} of ${pageCount}` : "Loading…"}
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {rows.data && rows.data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid="report-empty">
              No videos match these filters.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Video</TableHead>
                  <TableHead>Period</TableHead>
                  <TableHead>Owner</TableHead>
                  <TableHead>Editor</TableHead>
                  <TableHead>Category · type</TableHead>
                  <TableHead>Review</TableHead>
                  <TableHead>Submitted</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(rows.data?.items ?? []).map((r) => (
                  <TableRow key={r.asset_id}>
                    <TableCell className="max-w-56">
                      <Link
                        href={`/content/${r.asset_id}`}
                        className="font-medium underline-offset-2 hover:underline"
                      >
                        {r.title}
                      </Link>
                      <div className="flex flex-wrap gap-1 pt-1">
                        {r.ingest_source === "scan" ? (
                          <Badge variant="outline">historical</Badge>
                        ) : null}
                        {r.revision_of_asset_id ? (
                          <Badge
                            variant="outline"
                            title={`Correction ${r.revision_index} of ${r.chain_length - 1}`}
                          >
                            corrected version
                          </Badge>
                        ) : null}
                        {r.chain_length > 1 && !r.revision_of_asset_id ? (
                          <Badge variant="outline">has corrections</Badge>
                        ) : null}
                        {r.approved_unit ? <Badge>approved unit</Badge> : null}
                        {r.asset_status !== "active" ? (
                          <Badge variant="destructive">{r.asset_status}</Badge>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className="tabular-nums">{r.production_period ?? "—"}</TableCell>
                    <TableCell>{r.owner?.name ?? "Unknown"}</TableCell>
                    <TableCell>
                      {r.attributed_editor ? (
                        <span
                          title={
                            r.uploader && r.uploader.id !== r.attributed_editor.id
                              ? `Uploaded by ${r.uploader.email}`
                              : undefined
                          }
                        >
                          {personLabel(r)}
                          {r.uploader && r.uploader.id !== r.attributed_editor.id
                            ? " (on behalf)"
                            : ""}
                        </span>
                      ) : (
                        <Badge variant="outline">unknown editor</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-xs">
                      {r.product_category?.name ?? "—"} · {r.content_type?.name ?? "—"}
                    </TableCell>
                    <TableCell>
                      {statusBadge(r.handoff_status)}
                      {r.reviewed_at ? (
                        <div className="text-xs text-muted-foreground">
                          {r.reviewed_by ?? "?"} · {formatDateTime(r.reviewed_at)}
                        </div>
                      ) : null}
                      {r.review_note ? <div className="text-xs">“{r.review_note}”</div> : null}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDateTime(r.submitted_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {pageCount > 1 ? (
            <div className="flex items-center justify-end gap-2 pt-3">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= pageCount}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </>
  );
}
