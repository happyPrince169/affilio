"use client";

import type { UploadOptions } from "@affilio/shared-types";
import {
  CircleCheck,
  Copy,
  LoaderCircle,
  Pause,
  Play,
  RotateCcw,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatBytes } from "@/lib/format";
import {
  type BatchDefaults,
  type Classification,
  type ItemState,
  type QueueItem,
  type Summary,
  displayName,
  effectiveClassification,
} from "@/lib/uploads/queue";

interface UploadQueueProps {
  items: QueueItem[];
  defaults: BatchDefaults;
  options: UploadOptions;
  summary: Summary;
  onOverride: (localId: string, override: Classification) => void;
  onPause: (localId: string) => void;
  onResume: (localId: string) => void;
  onCancel: (localId: string) => void;
  onRetry: (localId: string) => void;
  onRemove: (localId: string) => void;
  onClearFinished: () => void;
}

const STATE_LABEL: Record<ItemState, string> = {
  queued: "Queued",
  needs_file: "Needs file",
  creating: "Starting",
  uploading: "Uploading",
  paused: "Paused",
  processing: "Processing",
  completed: "Completed",
  duplicate: "Duplicate",
  failed: "Failed",
  cancelled: "Cancelled",
};

function stateVariant(state: ItemState): "default" | "secondary" | "destructive" | "outline" {
  switch (state) {
    case "completed":
      return "default";
    case "failed":
      return "destructive";
    case "duplicate":
    case "cancelled":
    case "needs_file":
      return "outline";
    default:
      return "secondary";
  }
}

function Progress({ value, label }: { value: number; label: string }) {
  const pct = Math.round(value * 100);
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      className="h-2 w-full overflow-hidden rounded-full bg-muted"
    >
      <div
        className="h-full rounded-full bg-primary transition-[width]"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function UploadSummaryBar({
  summary,
  onClearFinished,
}: {
  summary: Summary;
  onClearFinished: () => void;
}) {
  if (summary.total === 0) return null;
  const finished = summary.completed + summary.duplicate + summary.failed + summary.cancelled;
  return (
    <div className="space-y-2 rounded-lg border p-3" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-medium">
          {finished === summary.total ? "Batch finished" : "Uploading batch"} · {summary.completed}{" "}
          of {summary.total} completed
          {summary.processing ? ` · ${summary.processing} processing` : ""}
          {summary.duplicate ? ` · ${summary.duplicate} duplicate` : ""}
          {summary.failed ? ` · ${summary.failed} failed` : ""}
          {summary.cancelled ? ` · ${summary.cancelled} cancelled` : ""}
        </span>
        <span className="text-muted-foreground">
          {formatBytes(summary.bytesDone)} / {formatBytes(summary.bytesTotal)}
        </span>
      </div>
      <Progress value={summary.progress} label="Overall progress" />
      {finished > 0 ? (
        <div className="flex justify-end">
          <Button variant="ghost" size="sm" onClick={onClearFinished}>
            Clear finished
          </Button>
        </div>
      ) : null}
    </div>
  );
}

export function UploadQueue({
  items,
  defaults,
  options,
  summary,
  onOverride,
  onPause,
  onResume,
  onCancel,
  onRetry,
  onRemove,
  onClearFinished,
}: UploadQueueProps) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="queue-empty">
        No videos queued yet. Add files above; each one uploads independently and resumes after an
        interruption.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      <UploadSummaryBar summary={summary} onClearFinished={onClearFinished} />
      <ul className="divide-y rounded-lg border" aria-label="Upload queue">
        {items.map((item) => {
          const cls = effectiveClassification(item, defaults);
          const name = displayName(item.name);
          const fraction = item.size ? item.offset / item.size : 0;
          const running = item.state === "uploading" || item.state === "creating";
          return (
            <li
              key={item.localId}
              className="grid gap-2 px-3 py-2 text-sm sm:grid-cols-[minmax(0,1fr)_auto]"
            >
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate font-medium" title={name}>
                    {name}
                  </span>
                  <span className="text-xs text-muted-foreground">{formatBytes(item.size)}</span>
                  <Badge variant={stateVariant(item.state)} className="gap-1">
                    {item.state === "processing" || running ? (
                      <LoaderCircle className="size-3 animate-spin" aria-hidden="true" />
                    ) : item.state === "completed" ? (
                      <CircleCheck className="size-3" aria-hidden="true" />
                    ) : item.state === "duplicate" ? (
                      <Copy className="size-3" aria-hidden="true" />
                    ) : item.state === "failed" ? (
                      <TriangleAlert className="size-3" aria-hidden="true" />
                    ) : null}
                    {STATE_LABEL[item.state]}
                  </Badge>
                  {item.replacesAssetId ? <Badge variant="outline">Corrected version</Badge> : null}
                </div>
                {item.state === "uploading" ||
                item.state === "paused" ||
                item.state === "queued" ? (
                  <Progress value={fraction} label={`Progress of ${name}`} />
                ) : null}
                {item.state === "processing" ? (
                  <p className="text-xs text-muted-foreground">
                    All bytes received. The server is verifying and filing the video; it is not in
                    the library yet.
                  </p>
                ) : null}
                {item.state === "completed" && item.mediaAssetId ? (
                  <p className="text-xs">
                    In the library, pending review ·{" "}
                    <Link href={`/content/${item.mediaAssetId}`} className="underline">
                      open
                    </Link>
                  </p>
                ) : null}
                {item.state === "duplicate" ? (
                  <p className="text-xs text-muted-foreground">
                    Identical content is already in the library; nothing was added.
                  </p>
                ) : null}
                {item.state === "needs_file" ? (
                  <p className="text-xs text-muted-foreground">
                    Interrupted before the reload at {formatBytes(item.offset)}. Select the same
                    file again to continue; it is checked against the uploaded part first.
                  </p>
                ) : null}
                {item.error ? (
                  <p className="text-xs text-destructive" role="status">
                    {item.error}
                  </p>
                ) : null}
                {item.state === "queued" || item.state === "paused" || item.state === "failed" ? (
                  <div className="flex flex-wrap gap-2">
                    <Select
                      value={cls.productCategoryId ?? ""}
                      onValueChange={(v) =>
                        onOverride(item.localId, { ...item.override, productCategoryId: v })
                      }
                    >
                      <SelectTrigger
                        size="sm"
                        className="h-7 w-40 text-xs"
                        aria-label={`Product category for ${name}`}
                      >
                        <SelectValue placeholder="Product category" />
                      </SelectTrigger>
                      <SelectContent>
                        {options.product_categories.map((c) => (
                          <SelectItem key={c.id} value={c.id}>
                            {c.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Select
                      value={cls.contentTypeId ?? ""}
                      onValueChange={(v) =>
                        onOverride(item.localId, { ...item.override, contentTypeId: v })
                      }
                    >
                      <SelectTrigger
                        size="sm"
                        className="h-7 w-36 text-xs"
                        aria-label={`Content type for ${name}`}
                      >
                        <SelectValue placeholder="Content type" />
                      </SelectTrigger>
                      <SelectContent>
                        {options.content_types.map((c) => (
                          <SelectItem key={c.id} value={c.id}>
                            {c.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                ) : null}
              </div>
              <div className="flex items-start gap-1 sm:justify-end">
                {running ? (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Pause ${name}`}
                    onClick={() => onPause(item.localId)}
                  >
                    <Pause />
                  </Button>
                ) : null}
                {item.state === "paused" ? (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Resume ${name}`}
                    onClick={() => onResume(item.localId)}
                  >
                    <Play />
                  </Button>
                ) : null}
                {item.state === "failed" && item.file ? (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Retry ${name}`}
                    onClick={() => onRetry(item.localId)}
                  >
                    <RotateCcw />
                  </Button>
                ) : null}
                {!["completed", "duplicate", "failed", "cancelled"].includes(item.state) ? (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Cancel ${name}`}
                    onClick={() => onCancel(item.localId)}
                  >
                    <X />
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Remove ${name} from the list`}
                    onClick={() => onRemove(item.localId)}
                  >
                    <Trash2 />
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
