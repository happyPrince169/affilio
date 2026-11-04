"use client";

import type { MediaAssetSummary } from "@affilio/shared-types";
import { cn } from "cn";
import { Film, RotateCcw } from "lucide-react";
import { useState } from "react";

import { MediaThumbnail } from "@/components/media/media-thumbnail";
import { Button } from "@/components/ui/button";
import { absoluteApiUrl } from "@/lib/api/media";

interface MediaPreviewProps {
  asset: MediaAssetSummary;
  className?: string;
}

/**
 * In-browser preview of a final or footage file, streamed from
 * `/api/v1/media/assets/{id}/stream` (same origin, Range requests for seeking). Falls back to
 * the thumbnail with a clear message when there is nothing playable or the browser cannot
 * decode the file (10-bit HEVC legacy finals are the usual case; an H.264 export fixes it).
 */
export function MediaPreview({ asset, className }: MediaPreviewProps) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const url = absoluteApiUrl(asset.preview_url);

  if (!url || failed) {
    return (
      <div className={cn("space-y-2", className)}>
        <MediaThumbnail asset={asset} className="aspect-[9/16] w-full rounded-lg" />
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Film className="size-3" />
            {!url
              ? asset.status === "active"
                ? "No playable file for this asset"
                : "Preview unavailable: the file is missing or unsupported"
              : "The browser could not play this file (codec not supported); the thumbnail is shown instead"}
          </span>
          {url ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setFailed(false);
                setAttempt((n) => n + 1);
              }}
            >
              <RotateCcw /> Retry
            </Button>
          ) : null}
        </div>
      </div>
    );
  }
  return (
    <div className={cn("space-y-2", className)}>
      <video
        key={`${url}#${attempt}`}
        src={url}
        controls
        playsInline
        preload="metadata"
        poster={absoluteApiUrl(asset.thumbnail_url) ?? undefined}
        className="aspect-[9/16] w-full rounded-lg bg-black object-contain"
        onError={() => setFailed(true)}
        aria-label={`Preview of ${asset.title}`}
      />
      <p className="text-xs text-muted-foreground">
        Preview streams the stored file as is; it is not a publishing export.
      </p>
    </div>
  );
}
