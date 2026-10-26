import type { ProductStatus } from "@affilio/shared-types";
import { cn } from "cn";

import { Badge } from "@/components/ui/badge";

export const STATUS_LABEL: Record<ProductStatus, string> = {
  discovered: "Discovered",
  shortlisted: "Shortlisted",
  testing: "Testing",
  winner: "Winner",
  paused: "Paused",
  archived: "Archived",
};

const STATUS_CLASS: Record<ProductStatus, string> = {
  discovered: "",
  shortlisted: "border-brand/50 text-brand",
  testing: "border-warning/60 text-warning",
  winner: "border-success/50 bg-success/10 text-success",
  paused: "text-muted-foreground",
  archived: "text-muted-foreground line-through decoration-muted-foreground/60",
};

export function ProductStatusBadge({
  status,
  className,
}: {
  status: ProductStatus;
  className?: string;
}) {
  return (
    <Badge variant="outline" className={cn(STATUS_CLASS[status], className)}>
      {STATUS_LABEL[status]}
    </Badge>
  );
}
