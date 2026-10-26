"use client";

import type { ProductDetail as ProductDetailType } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, Pencil, TriangleAlert } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { LinkedVideos } from "@/components/products/linked-videos";
import { ProductStatusBadge, STATUS_LABEL } from "@/components/products/product-badges";
import { ProductFormDialog } from "@/components/products/product-form-dialog";
import { ProductOperationsCard } from "@/components/products/product-operations-card";
import { ProductStatusControl } from "@/components/products/product-status-control";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { productQueries } from "@/lib/api/products";
import { formatDateTime, formatMoney, formatPercent, formatRelative } from "@/lib/format";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b py-2 text-sm last:border-b-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium break-all">{value ?? "–"}</dd>
    </div>
  );
}

function ExternalUrl({ url }: { url: string | null }) {
  if (!url) return <span className="text-muted-foreground">unknown</span>;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex max-w-full items-center gap-1 text-brand hover:underline"
    >
      <span className="truncate">{url.replace(/^https?:\/\//, "")}</span>
      <ExternalLink className="size-3 shrink-0" />
    </a>
  );
}

function Detail({ product }: { product: ProductDetailType }) {
  return (
    <div className="space-y-6">
      <PageHeader
        title={product.name}
        description={
          [product.seller_name, product.category].filter(Boolean).join(" · ") || undefined
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {product.source === "meoscout" ? (
              <Badge variant="secondary" className="font-mono text-[10px]">
                MeoScout{product.source_ref ? ` #${product.source_ref}` : ""}
              </Badge>
            ) : null}
            <ProductStatusBadge status={product.status} />
            <ProductStatusControl product={product} />
            <ProductFormDialog
              product={product}
              trigger={
                <Button variant="outline" size="sm">
                  <Pencil /> Edit
                </Button>
              }
            />
            <Button asChild variant="ghost" size="sm">
              <Link href="/products">
                <ArrowLeft /> Products
              </Link>
            </Button>
          </div>
        }
      />

      {product.status === "winner" ? (
        <Alert>
          <TriangleAlert />
          <AlertTitle>Confirmed winner</AlertTitle>
          <AlertDescription>
            Set by a human on {formatDateTime(product.status_changed_at)}. See the audit trail below
            for the evidence recorded.
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>Product</CardTitle>
            <CardDescription>
              Values entered by the operator. Unknown fields stay empty; nothing is scraped.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <dl>
              <Row
                label="Platform"
                value={<span className="capitalize">{product.platform}</span>}
              />
              <Row label="Product URL" value={<ExternalUrl url={product.product_url} />} />
              <Row label="Affiliate URL" value={<ExternalUrl url={product.affiliate_url} />} />
              <Row
                label="Platform product ID"
                value={
                  product.platform_product_id ? (
                    <span className="font-mono text-xs">{product.platform_product_id}</span>
                  ) : (
                    <span className="text-muted-foreground">unknown</span>
                  )
                }
              />
              <Row label="Seller / shop" value={product.seller_name} />
              <Row label="Category" value={product.category} />
              <Row
                label="Observed price"
                value={
                  <>
                    {formatMoney(product.observed_price, product.currency)}
                    {product.price_observed_at ? (
                      <span className="ml-2 text-xs text-muted-foreground">
                        {formatRelative(product.price_observed_at)}
                      </span>
                    ) : null}
                  </>
                }
              />
              <Row
                label="Estimated commission"
                value={
                  product.estimated_commission_rate || product.estimated_commission_amount ? (
                    <>
                      {formatPercent(product.estimated_commission_rate)}
                      {product.estimated_commission_amount ? (
                        <> · {formatMoney(product.estimated_commission_amount, product.currency)}</>
                      ) : null}
                      <span className="ml-2 text-xs text-muted-foreground">estimate</span>
                    </>
                  ) : (
                    <span className="text-muted-foreground">unknown</span>
                  )
                }
              />
              <Row label="Created" value={formatDateTime(product.created_at)} />
              <Row label="Updated" value={formatDateTime(product.updated_at)} />
            </dl>
          </CardContent>
        </Card>

        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle>Images</CardTitle>
              <CardDescription>Reference URLs only; not downloaded.</CardDescription>
            </CardHeader>
            <CardContent>
              {product.images.length === 0 ? (
                <p className="text-sm text-muted-foreground">No images recorded.</p>
              ) : (
                <div className="grid grid-cols-3 gap-2">
                  {product.images.map((url) => (
                    <a key={url} href={url} target="_blank" rel="noopener noreferrer">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={url}
                        alt=""
                        loading="lazy"
                        className="aspect-square w-full rounded-md border object-cover"
                      />
                    </a>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Research notes</CardTitle>
            </CardHeader>
            <CardContent>
              {product.research_notes ? (
                <p className="text-sm whitespace-pre-wrap">{product.research_notes}</p>
              ) : (
                <p className="text-sm text-muted-foreground">No notes yet. Use Edit to add some.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <ProductOperationsCard product={product} />

      <LinkedVideos productId={product.id} />

      <Card>
        <CardHeader>
          <CardTitle>Status history</CardTitle>
          <CardDescription>Every manual transition, newest first.</CardDescription>
        </CardHeader>
        <CardContent>
          <ol className="relative space-y-4 border-l pl-5">
            {product.status_events.map((event) => (
              <li key={event.id} className="relative">
                <span className="absolute top-1.5 -left-[25px] size-2.5 rounded-full border-2 border-background bg-brand" />
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  {event.from_status ? (
                    <>
                      <span className="text-muted-foreground">
                        {STATUS_LABEL[event.from_status]}
                      </span>
                      <span className="text-muted-foreground">→</span>
                    </>
                  ) : null}
                  <ProductStatusBadge status={event.to_status} />
                  {event.confirmed ? (
                    <Badge variant="outline" className="text-[10px]">
                      confirmed
                    </Badge>
                  ) : null}
                  <span className="ml-auto text-xs text-muted-foreground">
                    {formatDateTime(event.created_at)} · {event.actor}
                  </span>
                </div>
                {event.note ? (
                  <p className="mt-1 text-xs text-muted-foreground">{event.note}</p>
                ) : null}
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </div>
  );
}

export function ProductDetail({ id }: { id: string }) {
  const query = useQuery(productQueries.detail(id));
  if (query.isPending) {
    return (
      <div className="space-y-6" aria-busy>
        <Skeleton className="h-8 w-72" />
        <div className="grid gap-6 lg:grid-cols-5">
          <Skeleton className="h-96 rounded-xl lg:col-span-3" />
          <Skeleton className="h-96 rounded-xl lg:col-span-2" />
        </div>
      </div>
    );
  }
  if (query.isError) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <div className="space-y-4">
        <Alert variant={notFound ? "default" : "destructive"}>
          <TriangleAlert />
          <AlertTitle>{notFound ? "Product not found" : "Could not load product"}</AlertTitle>
          <AlertDescription>{query.error.message}</AlertDescription>
        </Alert>
        <Button asChild variant="outline">
          <Link href="/products">
            <ArrowLeft /> Back to products
          </Link>
        </Button>
      </div>
    );
  }
  return <Detail product={query.data} />;
}
