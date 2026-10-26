import type { MediaAssetStatus, ScanJobStatus, ThumbnailStatus } from "@affilio/shared-types";
import { cn } from "cn";

import { Badge } from "@/components/ui/badge";

export function AssetStatusBadge({ status }: { status: MediaAssetStatus }) {
  if (status === "active") {
    return (
      <Badge variant="outline" className="border-success/40 text-success">
        Active
      </Badge>
    );
  }
  if (status === "missing") {
    return <Badge variant="destructive">Missing</Badge>;
  }
  return (
    <Badge variant="outline" className="border-warning/50 text-warning">
      Unsupported
    </Badge>
  );
}

export function ThumbnailStatusBadge({ status }: { status: ThumbnailStatus }) {
  const label: Record<ThumbnailStatus, string> = {
    pending: "Pending",
    ready: "Ready",
    failed: "Failed",
    skipped: "Skipped",
  };
  return (
    <Badge
      variant={status === "failed" ? "destructive" : "outline"}
      className={cn(status === "ready" && "border-success/40 text-success")}
    >
      {label[status]}
    </Badge>
  );
}

export function ScanStatusBadge({ status }: { status: ScanJobStatus }) {
  const styles: Record<ScanJobStatus, string> = {
    queued: "",
    running: "border-brand/50 text-brand",
    succeeded: "border-success/40 text-success",
    failed: "",
    cancelled: "",
  };
  return (
    <Badge
      variant={status === "failed" ? "destructive" : "outline"}
      className={cn("capitalize", styles[status])}
    >
      {status}
    </Badge>
  );
}
