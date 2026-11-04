"use client";

import type { CategoryFacet, ContentCategory } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { taxonomyQueries } from "@/lib/api/taxonomy";

const NONE = "__none__";

interface FacetSelectProps {
  facet: CategoryFacet;
  value: string | null;
  onChange: (id: string | null) => void;
  placeholder?: string;
  noneLabel?: string;
  /** Restrict the choices (e.g. the owners the caller may submit for). */
  only?: Set<string>;
  disabled?: boolean;
  className?: string;
  size?: "sm" | "default";
}

export function facetValues(
  categories: ContentCategory[],
  facet: CategoryFacet,
): ContentCategory[] {
  return categories
    .filter((c) => c.facet === facet && c.depth > 0 && c.status === "active")
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** Dropdown of the active values under one facet root (owner, product category, content type). */
export function FacetSelect({
  facet,
  value,
  onChange,
  placeholder = "Any",
  noneLabel = "Any",
  only,
  disabled,
  className,
  size,
}: FacetSelectProps) {
  const categories = useQuery(taxonomyQueries.categories());
  const items = facetValues(categories.data ?? [], facet).filter((c) => !only || only.has(c.id));
  return (
    <Select
      value={value ?? NONE}
      onValueChange={(v) => onChange(v === NONE ? null : v)}
      disabled={disabled || categories.isPending}
    >
      <SelectTrigger className={className} size={size} aria-label={placeholder}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE}>{noneLabel}</SelectItem>
        {items.map((c) => (
          <SelectItem key={c.id} value={c.id}>
            {c.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
