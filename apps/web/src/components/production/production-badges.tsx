import type { EditingStatus, ProductionStatus } from "@affilio/shared-types";
import { cn } from "cn";

import { Badge } from "@/components/ui/badge";

export const PRODUCTION_LABEL: Record<ProductionStatus, string> = {
  planned: "Planned",
  scheduled: "Scheduled",
  filming: "Filming",
  filmed: "Filmed",
  cancelled: "Cancelled",
};
export const EDITING_LABEL: Record<EditingStatus, string> = {
  not_started: "Not started",
  in_progress: "Editing",
  review: "In review",
  done: "Edit done",
};
const PRODUCTION_CLASS: Record<ProductionStatus, string> = {
  planned: "",
  scheduled: "border-brand/50 text-brand",
  filming: "border-warning/60 text-warning",
  filmed: "border-success/50 text-success",
  cancelled: "text-muted-foreground line-through",
};
const EDITING_CLASS: Record<EditingStatus, string> = {
  not_started: "text-muted-foreground",
  in_progress: "border-brand/50 text-brand",
  review: "border-warning/60 text-warning",
  done: "border-success/50 text-success",
};

export function ProductionStatusBadge({ status }: { status: ProductionStatus }) {
  return (
    <Badge variant="outline" className={cn(PRODUCTION_CLASS[status])}>
      {PRODUCTION_LABEL[status]}
    </Badge>
  );
}
export function EditingStatusBadge({ status }: { status: EditingStatus }) {
  return (
    <Badge variant="outline" className={cn(EDITING_CLASS[status])}>
      {EDITING_LABEL[status]}
    </Badge>
  );
}
