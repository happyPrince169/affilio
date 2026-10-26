import type { Metadata } from "next";
import { Suspense } from "react";

import { PublicationDetailRoute } from "@/components/distribution/publication-detail";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Publication" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <PublicationDetailRoute />
    </Suspense>
  );
}
