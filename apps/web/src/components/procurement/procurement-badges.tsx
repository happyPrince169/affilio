import type { PurchaseOrderStatus, PurchaseRequestStatus } from "@affilio/shared-types";
import { cn } from "cn";

import { Badge } from "@/components/ui/badge";

export const REQUEST_LABEL: Record<PurchaseRequestStatus, string> = {
  requested: "Requested",
  approved: "Approved",
  rejected: "Rejected",
  ordered: "Ordered",
  cancelled: "Cancelled",
};
export const ORDER_LABEL: Record<PurchaseOrderStatus, string> = {
  draft: "Draft",
  placed: "Placed",
  shipped: "Shipped",
  delivered: "Delivered",
  partially_received: "Partially received",
  received: "Received",
  cancelled: "Cancelled",
};

const REQUEST_CLASS: Record<PurchaseRequestStatus, string> = {
  requested: "border-warning/60 text-warning",
  approved: "border-brand/50 text-brand",
  rejected: "",
  ordered: "border-success/50 text-success",
  cancelled: "text-muted-foreground",
};
const ORDER_CLASS: Record<PurchaseOrderStatus, string> = {
  draft: "text-muted-foreground",
  placed: "border-brand/50 text-brand",
  shipped: "border-brand/50 text-brand",
  delivered: "border-warning/60 text-warning",
  partially_received: "border-warning/60 text-warning",
  received: "border-success/50 text-success",
  cancelled: "text-muted-foreground line-through",
};

export function RequestStatusBadge({ status }: { status: PurchaseRequestStatus }) {
  return (
    <Badge
      variant={status === "rejected" ? "destructive" : "outline"}
      className={cn(REQUEST_CLASS[status])}
    >
      {REQUEST_LABEL[status]}
    </Badge>
  );
}
export function OrderStatusBadge({ status }: { status: PurchaseOrderStatus }) {
  return (
    <Badge variant="outline" className={cn(ORDER_CLASS[status])}>
      {ORDER_LABEL[status]}
    </Badge>
  );
}
