import type { ProductSummary } from "@affilio/shared-types";
import { ExternalLink, Film } from "lucide-react";
import Link from "next/link";

import { ProductStatusBadge } from "@/components/products/product-badges";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatMoney, formatPercent, formatRelative } from "@/lib/format";

export function ProductTable({ products }: { products: ProductSummary[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Product</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Seller</TableHead>
            <TableHead>Category</TableHead>
            <TableHead className="text-right">Price</TableHead>
            <TableHead className="text-right">Est. commission</TableHead>
            <TableHead className="text-right">Videos</TableHead>
            <TableHead>Updated</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {products.map((product) => (
            <TableRow key={product.id}>
              <TableCell className="max-w-72">
                <div className="flex items-center gap-3">
                  {product.images[0] ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={product.images[0]}
                      alt=""
                      className="size-10 shrink-0 rounded-md border object-cover"
                      loading="lazy"
                    />
                  ) : (
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-md border bg-muted text-xs text-muted-foreground">
                      —
                    </span>
                  )}
                  <div className="min-w-0">
                    <Link
                      href={`/products/${product.id}`}
                      className="block truncate font-medium hover:text-brand hover:underline"
                    >
                      {product.name}
                    </Link>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <span className="capitalize">{product.platform}</span>
                      {product.platform_product_id ? (
                        <span className="font-mono">· {product.platform_product_id}</span>
                      ) : null}
                      {product.product_url ? (
                        <a
                          href={product.product_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 hover:text-foreground"
                        >
                          <ExternalLink className="size-3" /> link
                        </a>
                      ) : null}
                    </div>
                  </div>
                </div>
              </TableCell>
              <TableCell>
                <ProductStatusBadge status={product.status} />
              </TableCell>
              <TableCell className="text-muted-foreground">{product.seller_name ?? "–"}</TableCell>
              <TableCell className="text-muted-foreground">{product.category ?? "–"}</TableCell>
              <TableCell className="text-right font-mono text-xs">
                {formatMoney(product.observed_price, product.currency)}
              </TableCell>
              <TableCell className="text-right font-mono text-xs">
                {product.estimated_commission_rate || product.estimated_commission_amount ? (
                  <>
                    {formatPercent(product.estimated_commission_rate)}
                    {product.estimated_commission_amount ? (
                      <span className="text-muted-foreground">
                        {" "}
                        · {formatMoney(product.estimated_commission_amount, product.currency)}
                      </span>
                    ) : null}
                  </>
                ) : (
                  <span className="text-muted-foreground">unknown</span>
                )}
              </TableCell>
              <TableCell className="text-right">
                <span className="inline-flex items-center gap-1 font-mono text-xs">
                  <Film className="size-3 text-muted-foreground" /> {product.media_count}
                </span>
              </TableCell>
              <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
                {formatRelative(product.updated_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function ProductTableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div
      className="space-y-2 rounded-xl border bg-card p-4"
      aria-busy
      aria-label="Loading products"
    >
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-10 w-full" />
      ))}
    </div>
  );
}
