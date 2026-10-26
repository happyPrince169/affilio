import type { AssetCategory } from "@affilio/shared-types";
import { cn } from "cn";
import { FolderTree, Hand } from "lucide-react";

import { Badge } from "@/components/ui/badge";

export const SOURCE_LABEL: Record<AssetCategory["assignment_source"], string> = {
  folder: "from folder",
  manual: "manual",
  import: "import",
  ai_future: "ai",
};

export function CategoryBadge({
  category,
  className,
}: {
  category: AssetCategory;
  className?: string;
}) {
  const Icon = category.assignment_source === "folder" ? FolderTree : Hand;
  return (
    <Badge
      variant={category.is_primary ? "default" : "outline"}
      className={cn("gap-1 text-[10px]", className)}
      title={`${category.path} · ${SOURCE_LABEL[category.assignment_source]}`}
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
