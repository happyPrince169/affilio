"use client";

import { useQuery } from "@tanstack/react-query";
import { Package, Star } from "lucide-react";
import Link from "next/link";

import { ProductStatusBadge } from "@/components/products/product-badges";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { productQueries } from "@/lib/api/products";

/** Products that reference a media asset (shown on the asset detail page). */
export function LinkedProductsCard({ assetId }: { assetId: string }) {
  const linked = useQuery(productQueries.forAsset(assetId));
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Package className="size-4 text-brand" /> Linked products
        </CardTitle>
        <CardDescription>Products this video promotes. Link from a product page.</CardDescription>
      </CardHeader>
      <CardContent>
        {linked.isPending ? (
          <Skeleton className="h-10 w-full" />
        ) : linked.isError ? (
          <p className="text-sm text-destructive">{linked.error.message}</p>
        ) : linked.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">Not linked to any product yet.</p>
        ) : (
          <ul className="divide-y">
            {linked.data.map((item) => (
              <li key={item.link_id} className="flex items-center justify-between gap-3 py-2">
                <Link
                  href={`/products/${item.product.id}`}
                  className="truncate text-sm font-medium hover:text-brand hover:underline"
                >
                  {item.product.name}
                </Link>
                <div className="flex shrink-0 items-center gap-2">
                  {item.is_primary ? (
                    <Badge variant="outline" className="gap-1 border-brand/50 text-brand">
                      <Star className="size-3" /> Primary
                    </Badge>
                  ) : null}
                  <ProductStatusBadge status={item.product.status} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
