import type { Metadata } from "next";
import { Suspense } from "react";

import { PurchasesPage } from "@/components/procurement/purchases-page";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Purchases" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <PurchasesPage />
    </Suspense>
  );
}
