import type { Metadata } from "next";
import { Suspense } from "react";

import { OrderDetailRoute } from "@/components/procurement/order-detail";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Purchase order" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <OrderDetailRoute />
    </Suspense>
  );
}
