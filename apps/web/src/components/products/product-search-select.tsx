"use client";

import type { ProductSummary } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState } from "react";

import { ProductStatusBadge } from "@/components/products/product-badges";
import { Input } from "@/components/ui/input";
import { productQueries } from "@/lib/api/products";

interface ProductSearchSelectProps {
  value: ProductSummary | null;
  onChange: (product: ProductSummary | null) => void;
  exclude?: Set<string>;
}

/** Compact product picker used by purchase requests and production jobs. */
export function ProductSearchSelect({ value, onChange, exclude }: ProductSearchSelectProps) {
  const [q, setQ] = useState("");
  const products = useQuery(productQueries.list({ q: q || undefined, page_size: 8, sort: "name" }));
  return (
    <div className="space-y-2">
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search products…"
          className="pl-8"
          aria-label="Search products"
        />
      </div>
      <div className="max-h-48 overflow-auto rounded-md border">
        {products.isPending ? (
          <p className="p-3 text-sm text-muted-foreground">Loading…</p>
        ) : products.isError ? (
          <p className="p-3 text-sm text-destructive">{products.error.message}</p>
        ) : products.data.items.length === 0 ? (
          <p className="p-3 text-sm text-muted-foreground">No products found.</p>
        ) : (
          <ul className="divide-y">
            {products.data.items.map((product) => {
              const disabled = exclude?.has(product.id) ?? false;
              const selected = value?.id === product.id;
              return (
                <li key={product.id}>
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onChange(selected ? null : product)}
                    aria-pressed={selected}
                    className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm outline-none hover:bg-muted focus-visible:bg-muted disabled:opacity-40 ${
                      selected ? "bg-accent" : ""
                    }`}
                  >
                    <span className="truncate">{product.name}</span>
                    <ProductStatusBadge status={product.status} className="shrink-0" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
