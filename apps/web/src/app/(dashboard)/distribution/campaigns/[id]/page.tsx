import type { Metadata } from "next";
import { Suspense } from "react";

import { CampaignDetailRoute } from "@/components/distribution/campaign-detail";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Campaign" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <CampaignDetailRoute />
    </Suspense>
  );
}
