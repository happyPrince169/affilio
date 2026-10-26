import type { Metadata } from "next";
import { Suspense } from "react";

import { CampaignsList } from "@/components/distribution/campaigns-list";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Distribution" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <CampaignsList />
    </Suspense>
  );
}
