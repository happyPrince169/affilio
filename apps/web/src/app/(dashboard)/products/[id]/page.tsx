import type { Metadata } from "next";
import { Suspense } from "react";

import { ProductDetailRoute } from "@/components/products/product-detail-route";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Product" };

export default function ProductPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <ProductDetailRoute />
    </Suspense>
  );
}
