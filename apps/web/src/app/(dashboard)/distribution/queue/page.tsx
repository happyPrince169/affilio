import type { Metadata } from "next";
import { Suspense } from "react";

import { PublishingQueue } from "@/components/distribution/publishing-queue";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Publishing queue" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <PublishingQueue />
    </Suspense>
  );
}
