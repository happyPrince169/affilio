import type { Metadata } from "next";
import { Suspense } from "react";

import { AssetDetailRoute } from "@/components/media/asset-detail-route";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Media asset" };

export default function MediaAssetPage() {
  // useParams() runs at request time; the Suspense boundary lets the shell prerender.
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <AssetDetailRoute />
    </Suspense>
  );
}
