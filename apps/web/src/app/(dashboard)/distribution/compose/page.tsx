import type { Metadata } from "next";
import { Suspense } from "react";

import { Composer } from "@/components/distribution/composer";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Composer" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <Composer />
    </Suspense>
  );
}
