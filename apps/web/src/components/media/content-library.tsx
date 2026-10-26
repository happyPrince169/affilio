"use client";

import type {
  HandoffStatus,
  MediaAssetFilters,
  MediaAssetKind,
  MediaAssetStatus,
  MediaSortKey,
} from "@affilio/shared-types";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { HandoffSummaryCard } from "@/components/media/handoff-summary-card";
import { MediaEmptyState } from "@/components/media/media-empty-state";
import { MediaFilters } from "@/components/media/media-filters";
import { MediaGrid, MediaGridSkeleton } from "@/components/media/media-grid";
import { MediaPagination } from "@/components/media/media-pagination";
import { ScanHistory } from "@/components/media/scan-history";
import { ScanStatusCard } from "@/components/media/scan-status-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { mediaQueries } from "@/lib/api/media";
import { formatBytes, formatDuration } from "@/lib/format";

const PAGE_SIZE = 24;
const STATUSES: MediaAssetStatus[] = ["active", "missing", "unsupported"];
const SORTS: MediaSortKey[] = ["newest", "oldest", "title", "duration", "size", "last_seen"];

function parseFilters(params: URLSearchParams): MediaAssetFilters {
  const status = params.get("status");
  const orientation = params.get("orientation");
  const sort = params.get("sort");
  const hasProxy = params.get("has_proxy");
  const kind = params.get("asset_kind");
  const handoff = params.get("handoff_status");
  return {
    q: params.get("q") ?? undefined,
    asset_kind: kind === "final" || kind === "footage" ? (kind as MediaAssetKind) : undefined,
    handoff_status:
      handoff === "pending_review" || handoff === "approved" || handoff === "rejected"
        ? (handoff as HandoffStatus)
        : undefined,
    status: STATUSES.includes(status as MediaAssetStatus)
      ? (status as MediaAssetStatus)
      : undefined,
    orientation:
      orientation === "vertical" || orientation === "horizontal" || orientation === "square"
        ? orientation
        : undefined,
    has_proxy: hasProxy === "true" ? true : hasProxy === "false" ? false : undefined,
    duplicates_only: params.get("duplicates_only") === "true" || undefined,
    sort: SORTS.includes(sort as MediaSortKey) ? (sort as MediaSortKey) : "newest",
    page: Math.max(1, Number(params.get("page") ?? 1) || 1),
    page_size: PAGE_SIZE,
  };
}

function serializeFilters(filters: MediaAssetFilters): string {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.asset_kind) params.set("asset_kind", filters.asset_kind);
  if (filters.handoff_status) params.set("handoff_status", filters.handoff_status);
  if (filters.status) params.set("status", filters.status);
  if (filters.orientation) params.set("orientation", filters.orientation);
  if (filters.has_proxy !== undefined) params.set("has_proxy", String(filters.has_proxy));
  if (filters.duplicates_only) params.set("duplicates_only", "true");
  if (filters.sort && filters.sort !== "newest") params.set("sort", filters.sort);
  if (filters.page && filters.page > 1) params.set("page", String(filters.page));
  const query = params.toString();
  return query ? `?${query}` : "";
}

export function ContentLibrary() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const filters = useMemo(() => parseFilters(searchParams), [searchParams]);

  const setFilters = useCallback(
    (next: MediaAssetFilters) => {
      router.replace(`${pathname}${serializeFilters(next)}`, { scroll: false });
    },
    [router, pathname],
  );

  const assets = useQuery({ ...mediaQueries.assets(filters), placeholderData: keepPreviousData });
  const stats = useQuery(mediaQueries.stats());
  const scanStatus = useQuery(mediaQueries.scanStatus());

  const hasFilters =
    Boolean(filters.q) ||
    Boolean(filters.status) ||
    Boolean(filters.orientation) ||
    filters.has_proxy !== undefined ||
    Boolean(filters.duplicates_only) ||
    Boolean(filters.asset_kind) ||
    Boolean(filters.handoff_status);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Content Library"
        description="Videos indexed from the originals root on the NAS. Originals and proxies are read-only; AFFILIO only writes thumbnails."
        actions={
          stats.data ? (
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary" className="font-mono">
                {stats.data.total} assets
              </Badge>
              <Badge variant="outline" className="font-mono">
                {formatBytes(stats.data.total_bytes)}
              </Badge>
              <Badge variant="outline" className="font-mono">
                {formatDuration(stats.data.total_duration_seconds)}
              </Badge>
              {stats.data.thumbnails_pending > 0 ? (
                <Badge variant="outline" className="font-mono text-brand">
                  {stats.data.thumbnails_pending} thumbnails pending
                </Badge>
              ) : null}
            </div>
          ) : null
        }
      />

      <ScanStatusCard />
      <HandoffSummaryCard />

      <div className="flex flex-wrap items-center gap-3">
        <Tabs
          value={filters.asset_kind ?? "all"}
          onValueChange={(value) =>
            setFilters({
              ...filters,
              asset_kind: value === "all" ? undefined : (value as MediaAssetKind),
              handoff_status: value === "final" ? filters.handoff_status : undefined,
              page: 1,
            })
          }
        >
          <TabsList>
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="footage">Footage</TabsTrigger>
            <TabsTrigger value="final">Finals</TabsTrigger>
          </TabsList>
        </Tabs>
        {filters.asset_kind === "final" ? (
          <Tabs
            value={filters.handoff_status ?? "any"}
            onValueChange={(value) =>
              setFilters({
                ...filters,
                handoff_status: value === "any" ? undefined : (value as HandoffStatus),
                page: 1,
              })
            }
          >
            <TabsList>
              <TabsTrigger value="any">Any review state</TabsTrigger>
              <TabsTrigger value="pending_review">Pending</TabsTrigger>
              <TabsTrigger value="approved">Approved</TabsTrigger>
              <TabsTrigger value="rejected">Rejected</TabsTrigger>
            </TabsList>
          </Tabs>
        ) : null}
      </div>

      <MediaFilters filters={filters} onChange={setFilters} disabled={assets.isPending} />

      {assets.isPending ? (
        <MediaGridSkeleton />
      ) : assets.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load the media library</AlertTitle>
          <AlertDescription>
            <p>{assets.error.message}</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => void assets.refetch()}
            >
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : assets.data.items.length === 0 ? (
        <MediaEmptyState
          scanStatus={scanStatus.data}
          hasFilters={hasFilters}
          onClearFilters={() => setFilters({ sort: filters.sort, page: 1, page_size: PAGE_SIZE })}
        />
      ) : (
        <div className={assets.isFetching ? "opacity-70 transition-opacity" : undefined}>
          <MediaGrid assets={assets.data.items} />
          <div className="mt-4">
            <MediaPagination
              page={assets.data.page}
              totalPages={assets.data.total_pages}
              total={assets.data.total}
              pageSize={assets.data.page_size}
              onPageChange={(page) => setFilters({ ...filters, page })}
            />
          </div>
        </div>
      )}

      <ScanHistory />
    </div>
  );
}
