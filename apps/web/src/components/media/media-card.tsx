import type { MediaAssetSummary } from "@affilio/shared-types";
import { Copy, Layers } from "lucide-react";
import Link from "next/link";

import { HandoffBadge } from "@/components/media/handoff-badge";
import { AssetStatusBadge } from "@/components/media/media-badges";
import { MediaThumbnail } from "@/components/media/media-thumbnail";
import { Badge } from "@/components/ui/badge";
import { codecLabel, formatBytes, formatDuration, formatResolution } from "@/lib/format";

export function MediaCard({ asset }: { asset: MediaAssetSummary }) {
  return (
    <Link
      href={`/content/${asset.id}`}
      className="group flex flex-col overflow-hidden rounded-xl border bg-card text-card-foreground shadow-xs transition-colors outline-none hover:border-brand/50 focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="relative">
        <MediaThumbnail asset={asset} className="aspect-[4/5] w-full" />
        {asset.duration_seconds ? (
          <span className="absolute right-2 bottom-2 rounded-md bg-black/70 px-1.5 py-0.5 font-mono text-[11px] text-white">
            {formatDuration(asset.duration_seconds)}
          </span>
        ) : null}
        {asset.status !== "active" ? (
          <span className="absolute top-2 left-2">
            <AssetStatusBadge status={asset.status} />
          </span>
        ) : asset.asset_kind === "final" && asset.handoff_status ? (
          <span className="absolute top-2 left-2">
            <HandoffBadge status={asset.handoff_status} className="bg-background/90" />
          </span>
        ) : null}
        {asset.duplicate_of_id ? (
          <span
            title="Duplicate of another asset"
            className="absolute top-2 right-2 rounded-md bg-black/70 p-1 text-white"
          >
            <Copy className="size-3.5" />
          </span>
        ) : null}
      </div>
      <div className="space-y-2 p-3">
        <p className="truncate text-sm font-medium" title={asset.file_name ?? asset.title}>
          {asset.title}
        </p>
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
          <span className="font-mono">{formatResolution(asset.width, asset.height)}</span>
          {asset.orientation ? <span className="capitalize">· {asset.orientation}</span> : null}
          {asset.video_codec ? <span>· {codecLabel(asset.video_codec)}</span> : null}
          {asset.bit_depth && asset.bit_depth > 8 ? <span>· {asset.bit_depth}-bit</span> : null}
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="font-mono text-[11px] text-muted-foreground">
            {formatBytes(asset.file_size)}
          </span>
          {asset.has_proxy ? (
            <Badge variant="secondary" className="gap-1 text-[10px]">
              <Layers className="size-3" /> Proxy
            </Badge>
          ) : null}
        </div>
      </div>
    </Link>
  );
}
