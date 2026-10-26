import type { Metadata } from "next";
import { Suspense } from "react";

import { ProductCatalog } from "@/components/products/product-catalog";
import { ProductTableSkeleton } from "@/components/products/product-table";

export const metadata: Metadata = { title: "Products" };

export default function ProductsPage() {
  return (
    <Suspense fallback={<ProductTableSkeleton />}>
      <ProductCatalog />
    </Suspense>
  );
}
