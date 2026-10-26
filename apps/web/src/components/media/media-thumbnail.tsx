"use client";

import type { MediaAssetSummary } from "@affilio/shared-types";
import { cn } from "cn";
import { Film, FileWarning, ImageOff, LoaderCircle } from "lucide-react";
import { useState } from "react";

import { absoluteApiUrl } from "@/lib/api/media";

interface MediaThumbnailProps {
  asset: Pick<MediaAssetSummary, "thumbnail_url" | "thumbnail_status" | "status" | "title">;
  className?: string;
}

function Placeholder({ asset }: MediaThumbnailProps) {
  const base = "flex size-full flex-col items-center justify-center gap-2 text-muted-foreground";
  if (asset.status === "unsupported") {
    return (
      <div className={base}>
        <FileWarning className="size-6" />
        <span className="text-[11px]">Unsupported</span>
      </div>
    );
  }
  if (asset.status === "missing") {
    return (
      <div className={base}>
        <ImageOff className="size-6" />
        <span className="text-[11px]">File missing</span>
      </div>
    );
  }
  if (asset.thumbnail_status === "pending") {
    return (
      <div className={base}>
        <LoaderCircle className="size-6 animate-spin" />
        <span className="text-[11px]">Thumbnail pending</span>
      </div>
    );
  }
  return (
    <div className={base}>
      <Film className="size-6" />
      <span className="text-[11px]">
        {asset.thumbnail_status === "failed" ? "Thumbnail failed" : "No thumbnail"}
      </span>
    </div>
  );
}

/**
 * Thumbnail image served by the API (`/api/v1/media/assets/{id}/thumbnail`).
 * Plain <img>: the source origin is runtime-configured and responses carry their own
 * Cache-Control, so next/image's optimizer would add nothing but a remotePatterns constraint.
 */
export function MediaThumbnail({ asset, className }: MediaThumbnailProps) {
  const [failed, setFailed] = useState(false);
  const url = absoluteApiUrl(asset.thumbnail_url);

  return (
    <div className={cn("relative overflow-hidden bg-muted", className)}>
      {url && !failed ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={url}
          alt={asset.title}
          loading="lazy"
          decoding="async"
          className="size-full object-cover"
          onError={() => setFailed(true)}
        />
      ) : (
        <Placeholder asset={asset} />
      )}
    </div>
  );
}
