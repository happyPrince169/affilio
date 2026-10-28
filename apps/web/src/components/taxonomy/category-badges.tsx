import type { AssetCategory } from "@affilio/shared-types";
import { cn } from "cn";
import { FolderTree, Hand } from "lucide-react";

import { Badge } from "@/components/ui/badge";

export const SOURCE_LABEL: Record<AssetCategory["assignment_source"], string> = {
  folder: "from folder rule",
  path: "from path",
  manual: "manual",
  import: "import",
  ai_future: "ai",
};

export const FACET_LABEL: Record<NonNullable<AssetCategory["facet"]>, string> = {
  production_owner: "Production owner",
  product_category: "Product category",
  content_type: "Content type",
};

export function CategoryBadge({
  category,
  className,
}: {
  category: AssetCategory;
  className?: string;
}) {
  const derived = category.assignment_source === "folder" || category.assignment_source === "path";
  const Icon = derived ? FolderTree : Hand;
  const facet = category.facet ? ` · ${FACET_LABEL[category.facet]}` : "";
  return (
    <Badge
      variant={category.is_primary ? "default" : "outline"}
      className={cn("gap-1 text-[10px]", className)}
      title={`${category.path}${facet} · ${SOURCE_LABEL[category.assignment_source]}`}
    >
      <Icon className="size-3" /> {category.name}
    </Badge>
  );
}

export function CategoryBadgeList({
  categories,
  max = 3,
  className,
}: {
  categories: AssetCategory[];
  max?: number;
  className?: string;
}) {
  if (categories.length === 0) return null;
  const shown = categories.slice(0, max);
  return (
    <div className={cn("flex flex-wrap gap-1", className)}>
      {shown.map((c) => (
        <CategoryBadge key={c.category_id} category={c} />
      ))}
      {categories.length > max ? (
        <span className="text-[10px] text-muted-foreground">+{categories.length - max}</span>
      ) : null}
    </div>
  );
}
