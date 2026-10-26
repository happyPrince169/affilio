import type { CampaignStatus, PublicationStatus } from "@affilio/shared-types";
import { cn } from "cn";

import { Badge } from "@/components/ui/badge";

export const PUBLICATION_LABEL: Record<PublicationStatus, string> = {
  draft: "Draft",
  ready_for_review: "Ready for review",
  approved: "Approved",
  scheduled: "Scheduled",
  ready_to_publish: "Ready to publish",
  publishing: "Publishing",
  published: "Published",
  failed: "Failed",
  cancelled: "Cancelled",
  needs_reconciliation: "Needs reconciliation",
};

const PUBLICATION_CLASS: Record<PublicationStatus, string> = {
  draft: "text-muted-foreground",
  ready_for_review: "border-warning/60 text-warning",
  approved: "border-brand/50 text-brand",
  scheduled: "border-brand/50 text-brand",
  ready_to_publish: "border-brand bg-brand/10 text-brand",
  publishing: "border-warning bg-warning/10 text-warning",
  published: "border-success/50 bg-success/10 text-success",
  failed: "border-destructive/50 text-destructive",
  cancelled: "text-muted-foreground line-through",
  needs_reconciliation: "border-destructive bg-destructive/10 text-destructive",
};

export const CAMPAIGN_LABEL: Record<CampaignStatus, string> = {
  draft: "Draft",
  active: "Active",
  completed: "Completed",
  archived: "Archived",
};

export function PublicationStatusBadge({
  status,
  className,
}: {
  status: PublicationStatus;
  className?: string;
}) {
  return (
    <Badge
      variant="outline"
      className={cn("whitespace-nowrap", PUBLICATION_CLASS[status], className)}
    >
      {PUBLICATION_LABEL[status]}
    </Badge>
  );
}

export function CampaignStatusBadge({ status }: { status: CampaignStatus }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        status === "active" && "border-success/50 text-success",
        status === "archived" && "text-muted-foreground line-through",
      )}
    >
      {CAMPAIGN_LABEL[status]}
    </Badge>
  );
}

export function EvidenceBadge({ kind }: { kind: "operator_reported" | "api_verified" | null }) {
  if (!kind) return null;
  return kind === "api_verified" ? (
    <Badge className="text-success-foreground bg-success">API verified</Badge>
  ) : (
    <Badge variant="secondary">Operator-reported</Badge>
  );
}
