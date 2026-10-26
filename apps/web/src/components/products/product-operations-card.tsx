"use client";

import type { ProductDetail } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { Clapperboard, ShoppingCart } from "lucide-react";
import Link from "next/link";

import { OrderStatusBadge } from "@/components/procurement/procurement-badges";
import { NewRequestDialog } from "@/components/procurement/request-dialogs";
import { ProductionStatusBadge } from "@/components/production/production-badges";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { procurementQueries } from "@/lib/api/procurement";
import { productionQueries } from "@/lib/api/production";

/** Procurement and production view of one product. Neither touches the research status. */
export function ProductOperationsCard({ product }: { product: ProductDetail }) {
  const procurement = useQuery(procurementQueries.product(product.id));
  const jobs = useQuery(
    productionQueries.jobs({ product_id: product.id, page_size: 10, sort: "newest" }),
  );
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-2">
            <div>
              <CardTitle className="flex items-center gap-2">
                <ShoppingCart className="size-4 text-brand" /> Purchases
              </CardTitle>
              <CardDescription>
                Units bought for filming. Independent of the research status.
              </CardDescription>
            </div>
            <NewRequestDialog
              product={product}
              trigger={
                <Button size="sm" variant="outline">
                  Request purchase
                </Button>
              }
            />
          </div>
        </CardHeader>
        <CardContent>
          {procurement.isPending ? (
            <Skeleton className="h-16 w-full" />
          ) : procurement.isError ? (
            <p className="text-sm text-destructive">{procurement.error.message}</p>
          ) : (
            <div className="space-y-3 text-sm">
              <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-muted-foreground">
                <span>
                  <span className="font-semibold text-foreground">
                    {procurement.data.open_requests}
                  </span>{" "}
                  open requests
                </span>
                <span>
                  <span className="font-semibold text-foreground">
                    {procurement.data.units_ordered}
                  </span>{" "}
                  ordered
                </span>
                <span>
                  <span className="font-semibold text-foreground">
                    {procurement.data.units_received}
                  </span>{" "}
                  received
                </span>
              </div>
              {procurement.data.orders.length === 0 ? (
                <p className="text-muted-foreground">Not on any purchase order yet.</p>
              ) : (
                <ul className="divide-y">
                  {procurement.data.orders.map((o) => (
                    <li key={o.id} className="flex items-center justify-between gap-2 py-2">
                      <Link
                        href={`/purchases/orders/${o.id}`}
                        className="font-mono text-xs hover:text-brand hover:underline"
                      >
                        {o.order_number}
                      </Link>
                      <span className="font-mono text-xs text-muted-foreground">
                        {o.units_received}/{o.units_ordered}
                      </span>
                      <OrderStatusBadge status={o.status} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clapperboard className="size-4 text-brand" /> Production jobs
          </CardTitle>
          <CardDescription>Shoots where this product was used on set.</CardDescription>
        </CardHeader>
        <CardContent>
          {jobs.isPending ? (
            <Skeleton className="h-16 w-full" />
          ) : jobs.isError ? (
            <p className="text-sm text-destructive">{jobs.error.message}</p>
          ) : jobs.data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">Not used in any production job yet.</p>
          ) : (
            <ul className="divide-y text-sm">
              {jobs.data.items.map((job) => (
                <li key={job.id} className="flex items-center justify-between gap-2 py-2">
                  <Link
                    href={`/production/${job.id}`}
                    className="min-w-0 truncate font-medium hover:text-brand hover:underline"
                  >
                    <span className="mr-2 font-mono text-xs text-muted-foreground">
                      {job.job_number}
                    </span>
                    {job.title}
                  </Link>
                  <ProductionStatusBadge status={job.status} />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
