"use client";

import { useParams } from "next/navigation";

import { ProductDetail } from "@/components/products/product-detail";

export function ProductDetailRoute() {
  const params = useParams<{ id: string }>();
  return <ProductDetail id={params.id} />;
}
