"use client";

import type { UploadSession, UploadStatus } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { absoluteApiUrl } from "@/lib/api/media";
import { uploadQueries } from "@/lib/api/uploads";
import { formatBytes, formatDateTime } from "@/lib/format";
import { displayName } from "@/lib/uploads/queue";

import type { CorrectionContext } from "./use-upload-queue";

interface MyUploadsProps {
  onCorrect: (ctx: CorrectionContext) => void;
}

const ALL = "__all__";
const STATUS_LABEL: Record<UploadStatus, string> = {
  created: "Not started",
  uploading: "Interrupted",
  uploaded: "Processing",
  verifying: "Processing",
  finalizing: "Processing",
  completed: "Completed",
  duplicate: "Duplicate",
  failed: "Failed",
  aborted: "Cancelled",
  expired: "Expired",
};

function reviewBadge(session: UploadSession) {
  const review = session.review;
  if (!review) return null;
  switch (review.handoff_status) {
    case "approved":
      return <Badge>Approved</Badge>;
    case "rejected":
      return <Badge variant="destructive">Rejected</Badge>;
    default:
      return <Badge variant="secondary">Pending review</Badge>;
  }
}

export function MyUploads({ onCorrect }: MyUploadsProps) {
  const [status, setStatus] = useState<string>(ALL);
  const [since, setSince] = useState("");
  const uploads = useQuery(uploadQueries.list({ limit: 200 }));

  const rows = useMemo(() => {
    const list = uploads.data ?? [];
    const sinceTs = since ? Date.parse(since) : null;
    return list.filter(
      (u) =>
        (status === ALL || u.status === status) &&
        (sinceTs === null || Number.isNaN(sinceTs) || Date.parse(u.created_at) >= sinceTs),
    );
  }, [uploads.data, status, since]);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="uploads-status">Status</Label>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger id="uploads-status" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All</SelectItem>
              {(Object.keys(STATUS_LABEL) as UploadStatus[]).map((s) => (
                <SelectItem key={s} value={s}>
                  {STATUS_LABEL[s]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="uploads-since">Submitted since</Label>
          <Input
            id="uploads-since"
            type="date"
            value={since}
            onChange={(e) => setSince(e.target.value)}
          />
        </div>
      </div>
      {uploads.isPending ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : uploads.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load your uploads</AlertTitle>
          <AlertDescription>{uploads.error.message}</AlertDescription>
        </Alert>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="my-uploads-empty">
          No submissions yet.
        </p>
      ) : (
        <ul className="divide-y rounded-lg border" aria-label="My uploads">
          {rows.map((u) => {
            const name = displayName(u.original_filename);
            const thumb = absoluteApiUrl(u.thumbnail_url);
            const rejected = u.review?.handoff_status === "rejected";
            return (
              <li key={u.id} className="flex gap-3 px-3 py-2 text-sm">
                <div className="flex size-16 shrink-0 items-center justify-center overflow-hidden rounded bg-muted">
                  {thumb ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={thumb} alt="" className="size-full object-cover" />
                  ) : (
                    <span className="text-[10px] text-muted-foreground">
                      {u.status === "completed" ? "No thumbnail yet" : STATUS_LABEL[u.status]}
                    </span>
                  )}
                </div>
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate font-medium" title={name}>
                      {name}
                    </span>
                    <Badge variant="outline">{STATUS_LABEL[u.status]}</Badge>
                    {u.status === "completed" ? reviewBadge(u) : null}
                    {u.replaces_asset_id ? (
                      <Badge variant="outline">Corrected version</Badge>
                    ) : null}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {formatDateTime(u.created_at)} · {formatBytes(u.expected_size)} ·{" "}
                    {u.production_owner_name} · {u.product_category.name} · {u.content_type.name}
                  </p>
                  {u.status === "duplicate" ? (
                    <p className="text-xs text-muted-foreground">
                      Identical content was already in the library; nothing was added.
                    </p>
                  ) : null}
                  {u.status === "failed" || u.status === "expired" ? (
                    <p className="text-xs text-destructive">
                      {displayName(u.failure_detail ?? "Upload failed")}
                    </p>
                  ) : null}
                  {u.review?.handoff_note ? (
                    <p className="text-xs">
                      <span className="font-medium">Reviewer:</span> {u.review.handoff_note}
                    </p>
                  ) : null}
                  <div className="flex flex-wrap gap-2">
                    {u.media_asset_id ? (
                      <Link href={`/content/${u.media_asset_id}`} className="text-xs underline">
                        Open in library
                      </Link>
                    ) : null}
                    {u.replaces_asset_id ? (
                      <Link href={`/content/${u.replaces_asset_id}`} className="text-xs underline">
                        Replaces an earlier version
                      </Link>
                    ) : null}
                    {rejected && u.media_asset_id ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-6 text-xs"
                        onClick={() =>
                          onCorrect({
                            replacesAssetId: u.media_asset_id as string,
                            title: name,
                            ownerId: u.production_owner.id,
                            productCategoryId: u.product_category.id,
                            contentTypeId: u.content_type.id,
                          })
                        }
                      >
                        Submit corrected version
                      </Button>
                    ) : null}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
