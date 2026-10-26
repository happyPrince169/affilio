import type { Metadata } from "next";
import { Suspense } from "react";

import { ProductionBoard } from "@/components/production/production-board";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Production" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <ProductionBoard />
    </Suspense>
  );
}
