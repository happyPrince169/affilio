import type { Metadata } from "next";
import { Suspense } from "react";

import { JobDetailRoute } from "@/components/production/job-detail";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Production job" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <JobDetailRoute />
    </Suspense>
  );
}
