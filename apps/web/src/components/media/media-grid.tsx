import type { MediaAssetSummary } from "@affilio/shared-types";

import { MediaCard } from "@/components/media/media-card";
import { Skeleton } from "@/components/ui/skeleton";

export function MediaGrid({ assets }: { assets: MediaAssetSummary[] }) {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6">
      {assets.map((asset) => (
        <MediaCard key={asset.id} asset={asset} />
      ))}
    </div>
  );
}

export function MediaGridSkeleton({ count = 12 }: { count?: number }) {
  return (
    <div
      className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6"
      aria-busy
      aria-label="Loading media"
    >
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="overflow-hidden rounded-xl border">
          <Skeleton className="aspect-[4/5] w-full rounded-none" />
          <div className="space-y-2 p-3">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        </div>
      ))}
    </div>
  );
}
