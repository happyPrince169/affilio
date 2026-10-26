import type { HandoffStatus } from "@affilio/shared-types";
import { cn } from "cn";

import { Badge } from "@/components/ui/badge";

export const HANDOFF_LABEL: Record<HandoffStatus, string> = {
  pending_review: "Pending review",
  approved: "Approved",
  rejected: "Rejected",
};

export function HandoffBadge({ status, className }: { status: HandoffStatus; className?: string }) {
  return (
    <Badge
      variant={status === "rejected" ? "destructive" : "outline"}
      className={cn(
        status === "approved" && "border-success/50 text-success",
        status === "pending_review" && "border-warning/60 text-warning",
        className,
      )}
    >
      {HANDOFF_LABEL[status]}
    </Badge>
  );
}
