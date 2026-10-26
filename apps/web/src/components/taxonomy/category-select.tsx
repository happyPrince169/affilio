"use client";

import type { ContentCategory } from "@affilio/shared-types";
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

interface CategorySelectProps {
  value: string | null;
  onChange: (id: string | null) => void;
  placeholder?: string;
  noneLabel?: string;
  allowNone?: boolean;
  exclude?: Set<string>;
  disabled?: boolean;
  className?: string;
  size?: "sm" | "default";
}

export function categoryLabel(c: ContentCategory): string {
  return `${"  ".repeat(c.depth)}${c.depth > 0 ? "↳ " : ""}${c.name}`;
}

/** Hierarchical category dropdown fed by the taxonomy query. */
export function CategorySelect({
  value,
  onChange,
  placeholder = "Category",
  noneLabel = "None",
  allowNone = true,
  exclude,
  disabled,
  className,
  size,
}: CategorySelectProps) {
  const categories = useQuery(taxonomyQueries.categories());
  const items = (categories.data ?? []).filter((c) => !exclude?.has(c.id));
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
        {allowNone ? <SelectItem value={NONE}>{noneLabel}</SelectItem> : null}
        {items.map((c) => (
          <SelectItem key={c.id} value={c.id}>
            {categoryLabel(c)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
