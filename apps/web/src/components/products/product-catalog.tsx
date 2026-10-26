"use client";

import type { ProductFilters, ProductSortKey, ProductStatus } from "@affilio/shared-types";
import { PRODUCT_SORT_KEYS, PRODUCT_STATUSES } from "@affilio/shared-types";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Package, Plus, Search, TriangleAlert, Upload, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { MediaPagination } from "@/components/media/media-pagination";
import { STATUS_LABEL } from "@/components/products/product-badges";
import { ImportDialog } from "@/components/products/import-dialog";
import { ProductFormDialog } from "@/components/products/product-form-dialog";
import { ProductTable, ProductTableSkeleton } from "@/components/products/product-table";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { productQueries } from "@/lib/api/products";

const PAGE_SIZE = 25;
const ALL = "all";
const SORT_LABEL: Record<ProductSortKey, string> = {
  newest: "Newest first",
  oldest: "Oldest first",
  name: "Name A–Z",
  updated: "Recently updated",
  price: "Highest price",
  commission: "Highest est. commission",
};

function parseFilters(params: URLSearchParams): ProductFilters {
  const status = params.get("status");
  const sort = params.get("sort");
  const hasMedia = params.get("has_media");
  return {
    q: params.get("q") ?? undefined,
    status: PRODUCT_STATUSES.includes(status as ProductStatus)
      ? (status as ProductStatus)
      : undefined,
    has_media: hasMedia === "true" ? true : hasMedia === "false" ? false : undefined,
    sort: PRODUCT_SORT_KEYS.includes(sort as ProductSortKey) ? (sort as ProductSortKey) : "newest",
    page: Math.max(1, Number(params.get("page") ?? 1) || 1),
    page_size: PAGE_SIZE,
  };
}

function serialize(filters: ProductFilters): string {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.status) params.set("status", filters.status);
  if (filters.has_media !== undefined) params.set("has_media", String(filters.has_media));
  if (filters.sort && filters.sort !== "newest") params.set("sort", filters.sort);
  if (filters.page && filters.page > 1) params.set("page", String(filters.page));
  const query = params.toString();
  return query ? `?${query}` : "";
}

export function ProductCatalog() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const filters = useMemo(() => parseFilters(searchParams), [searchParams]);
  const setFilters = useCallback(
    (next: ProductFilters) => router.replace(`${pathname}${serialize(next)}`, { scroll: false }),
    [router, pathname],
  );
  const update = (patch: Partial<ProductFilters>) => setFilters({ ...filters, ...patch, page: 1 });

  const [search, setSearch] = useState(filters.q ?? "");
  const [syncedQ, setSyncedQ] = useState(filters.q ?? "");
  if (syncedQ !== (filters.q ?? "")) {
    setSyncedQ(filters.q ?? "");
    setSearch(filters.q ?? "");
  }
  useEffect(() => {
    const handle = setTimeout(() => {
      if ((filters.q ?? "") !== search) update({ q: search || undefined });
    }, 300);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const products = useQuery({ ...productQueries.list(filters), placeholderData: keepPreviousData });
  const stats = useQuery(productQueries.stats());
  const hasFilters = Boolean(filters.q || filters.status || filters.has_media !== undefined);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Products"
        description="Shopee affiliate products under research. Commission figures are operator estimates until analytics (M4) reports actual earnings."
        actions={
          <div className="flex items-center gap-2">
            <ImportDialog
              trigger={
                <Button variant="outline">
                  <Upload /> Import from MeoScout
                </Button>
              }
            />
            <ProductFormDialog
              trigger={
                <Button>
                  <Plus /> New product
                </Button>
              }
            />
          </div>
        }
      />

      {stats.data ? (
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary" className="font-mono">
            {stats.data.total} products
          </Badge>
          {PRODUCT_STATUSES.filter((s) => stats.data.by_status[s] > 0).map((status) => (
            <button
              key={status}
              type="button"
              onClick={() => update({ status: filters.status === status ? undefined : status })}
              className="rounded-md outline-none focus-visible:ring-2"
            >
              <Badge
                variant={filters.status === status ? "default" : "outline"}
                className="cursor-pointer font-mono"
              >
                {stats.data.by_status[status]} {STATUS_LABEL[status].toLowerCase()}
              </Badge>
            </button>
          ))}
          <Badge variant="outline" className="font-mono">
            {stats.data.with_media} with videos
          </Badge>
        </div>
      ) : null}

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search name, seller, category, platform id…"
            className="pl-8"
            aria-label="Search products"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={filters.status ?? ALL}
            onValueChange={(value) =>
              update({ status: value === ALL ? undefined : (value as ProductStatus) })
            }
          >
            <SelectTrigger className="w-40" aria-label="Status filter">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All statuses</SelectItem>
              {PRODUCT_STATUSES.map((status) => (
                <SelectItem key={status} value={status}>
                  {STATUS_LABEL[status]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={filters.has_media === undefined ? ALL : filters.has_media ? "yes" : "no"}
            onValueChange={(value) =>
              update({ has_media: value === ALL ? undefined : value === "yes" })
            }
          >
            <SelectTrigger className="w-36" aria-label="Video filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Videos: any</SelectItem>
              <SelectItem value="yes">Has videos</SelectItem>
              <SelectItem value="no">No videos</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={filters.sort ?? "newest"}
            onValueChange={(value) => update({ sort: value as ProductSortKey })}
          >
            <SelectTrigger className="w-48" aria-label="Sort order">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PRODUCT_SORT_KEYS.map((key) => (
                <SelectItem key={key} value={key}>
                  {SORT_LABEL[key]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {hasFilters ? (
            <Button variant="ghost" size="sm" onClick={() => setFilters({ sort: filters.sort })}>
              <X /> Clear
            </Button>
          ) : null}
        </div>
      </div>

      {products.isPending ? (
        <ProductTableSkeleton />
      ) : products.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load products</AlertTitle>
          <AlertDescription>
            <p>{products.error.message}</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => void products.refetch()}
            >
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : products.data.items.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-16 text-center">
          <Package className="size-8 text-muted-foreground" />
          <h2 className="text-lg font-semibold">
            {hasFilters ? "No products match these filters" : "No products yet"}
          </h2>
          <p className="max-w-md text-sm text-muted-foreground">
            {hasFilters
              ? "Try a different search or clear the filters."
              : "Add the first product you are researching. You can link videos from the Content Library to it afterwards."}
          </p>
          {hasFilters ? (
            <Button variant="outline" size="sm" onClick={() => setFilters({ sort: filters.sort })}>
              Clear filters
            </Button>
          ) : (
            <ProductFormDialog
              trigger={
                <Button size="sm">
                  <Plus /> New product
                </Button>
              }
            />
          )}
        </div>
      ) : (
        <div className={products.isFetching ? "opacity-70 transition-opacity" : undefined}>
          <ProductTable products={products.data.items} />
          <div className="mt-4">
            <MediaPagination
              page={products.data.page}
              totalPages={products.data.total_pages}
              total={products.data.total}
              pageSize={products.data.page_size}
              onPageChange={(page) => setFilters({ ...filters, page })}
            />
          </div>
        </div>
      )}
    </div>
  );
}
