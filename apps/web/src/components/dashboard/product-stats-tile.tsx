"use client";

import { useQuery } from "@tanstack/react-query";
import { Package } from "lucide-react";
import Link from "next/link";

import { Card, CardContent } from "@/components/ui/card";
import { productQueries } from "@/lib/api/products";

export function ProductStatsTile() {
  const stats = useQuery(productQueries.stats());
  const value = stats.data ? stats.data.total.toLocaleString() : "—";
  const note = stats.data
    ? stats.data.total === 0
      ? "No products yet. Add the first one under Products."
      : `${stats.data.by_status.testing} testing · ${stats.data.by_status.winner} winners · ${stats.data.with_media} with videos`
    : stats.isError
      ? "Product stats unavailable (API offline)."
      : "Loading product stats…";

  return (
    <Link href="/products" className="rounded-xl outline-none focus-visible:ring-2">
      <Card className="h-full gap-3 py-5 transition-colors hover:border-brand/50">
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-muted-foreground">Products tracked</span>
            <Package className="size-4 text-muted-foreground" />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-semibold tracking-tight">{value}</span>
            <span className="rounded-md border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
              M2
            </span>
          </div>
          <p className="text-xs text-muted-foreground">{note}</p>
        </CardContent>
      </Card>
    </Link>
  );
}
