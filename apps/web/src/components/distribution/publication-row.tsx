"use client";

import type { PublicationSummary } from "@affilio/shared-types";
import { Clock3, Film, Link2, TriangleAlert } from "lucide-react";
import Link from "next/link";

import { useAuth } from "@/components/auth/auth-provider";
import { EvidenceBadge, PublicationStatusBadge } from "@/components/distribution/badges";
import { MockPublicationBadge, UploadInline } from "@/components/distribution/upload-progress";
import { MediaThumbnail } from "@/components/media/media-thumbnail";
import { formatInZone } from "@/lib/format";

export function PublicationRow({
  item,
  showCampaign = true,
}: {
  item: PublicationSummary;
  showCampaign?: boolean;
}) {
  const { timezone } = useAuth();
  return (
    <Link
      href={`/distribution/publications/${item.id}`}
      className="flex gap-3 rounded-lg border bg-card p-3 text-sm shadow-xs transition-colors outline-none hover:border-brand/50 focus-visible:ring-2"
    >
      <MediaThumbnail
        asset={{
          thumbnail_url: item.thumbnail_url,
          thumbnail_status: item.thumbnail_url ? "ready" : "pending",
          status: "active",
          title: item.media_title,
        }}
        className="hidden h-16 w-28 shrink-0 rounded-md sm:block"
      />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <PublicationStatusBadge status={item.status} />
          <EvidenceBadge kind={item.evidence_kind} />
          {item.status === "published" && item.target_is_mock ? <MockPublicationBadge /> : null}
          {item.approval_stale ? (
            <span className="inline-flex items-center gap-1 text-[11px] text-warning">
              <TriangleAlert className="size-3" /> approval outdated
            </span>
          ) : null}
          {item.classification_stale ? (
            <span className="inline-flex items-center gap-1 text-[11px] text-warning">
              <TriangleAlert className="size-3" /> video category changed
            </span>
          ) : null}
          {item.target_blocked_reason ? (
            <span className="inline-flex items-center gap-1 text-[11px] text-destructive">
              <TriangleAlert className="size-3" /> target blocked
            </span>
          ) : null}
        </div>
        <p className="truncate font-medium">
          {item.title || item.caption_preview || item.media_title}
        </p>
        <p className="truncate text-xs text-muted-foreground">
          <span className="font-medium text-foreground/80">{item.account_display_name}</span> ·{" "}
          {item.platform}
          {showCampaign ? ` · ${item.campaign_name}` : ""}
        </p>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <Film className="size-3" /> {item.media_file_name ?? item.media_title}
          </span>
          <span className="inline-flex items-center gap-1">
            <Link2 className="size-3" /> {item.link_count} link{item.link_count === 1 ? "" : "s"}
          </span>
          {item.scheduled_at ? (
            <span className="inline-flex items-center gap-1">
              <Clock3 className="size-3" /> {formatInZone(item.scheduled_at, timezone)}
            </span>
          ) : null}
          {item.published_at ? (
            <span>published {formatInZone(item.published_at, timezone)}</span>
          ) : null}
          {item.last_error && !item.upload ? (
            <span className="text-destructive">{item.last_error}</span>
          ) : null}
          <UploadInline item={item} />
        </div>
      </div>
    </Link>
  );
}
