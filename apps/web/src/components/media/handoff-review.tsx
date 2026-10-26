"use client";

import type { HandoffStatus, MediaAssetDetail } from "@affilio/shared-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleX, LoaderCircle, RotateCcw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { HandoffBadge } from "@/components/media/handoff-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { mediaQueries, reviewHandoff } from "@/lib/api/media";
import { formatDateTime } from "@/lib/format";

/** Human approval gate for a final video. Shown on the asset page for `asset_kind = final`. */
export function HandoffReviewCard({ asset }: { asset: MediaAssetDetail }) {
  const [target, setTarget] = useState<HandoffStatus | null>(null);
  const [note, setNote] = useState("");
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => reviewHandoff(asset.id, { status: target!, note: note.trim() || null }),
    onSuccess: (saved) => {
      toast.success(
        saved.handoff_status === "approved" ? "Final approved for publishing" : "Review recorded",
      );
      void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
      setTarget(null);
      setNote("");
    },
    onError: (error) => toast.error("Review not saved", { description: error.message }),
  });
  const needsNote = target === "approved" || target === "rejected";
  const status = asset.handoff_status ?? "pending_review";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Handoff review <HandoffBadge status={status} />
        </CardTitle>
        <CardDescription>
          Finished videos need a human decision before any publishing step (M3) can use them.
          {asset.handoff_reviewed_at
            ? ` Last reviewed ${formatDateTime(asset.handoff_reviewed_at)} by ${asset.handoff_reviewed_by ?? "unknown"}.`
            : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {asset.handoff_note ? (
          <p className="rounded-md bg-muted p-3 text-sm whitespace-pre-wrap">
            {asset.handoff_note}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {status !== "approved" ? (
            <Button
              size="sm"
              onClick={() => setTarget("approved")}
              disabled={asset.status !== "active"}
            >
              <CircleCheck /> Approve
            </Button>
          ) : null}
          {status !== "rejected" ? (
            <Button size="sm" variant="outline" onClick={() => setTarget("rejected")}>
              <CircleX /> Reject
            </Button>
          ) : null}
          {status !== "pending_review" ? (
            <Button size="sm" variant="ghost" onClick={() => setTarget("pending_review")}>
              <RotateCcw /> Back to pending
            </Button>
          ) : null}
        </div>
        {asset.status !== "active" ? (
          <p className="text-xs text-muted-foreground">
            A missing or unsupported file cannot be approved.
          </p>
        ) : null}
      </CardContent>

      <Dialog open={target !== null} onOpenChange={(open) => !open && setTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {target === "approved"
                ? "Approve this final?"
                : target === "rejected"
                  ? "Reject this final?"
                  : "Return to pending review?"}
            </DialogTitle>
            <DialogDescription>
              {needsNote
                ? "A note is required so the decision can be audited (what was checked, what must change)."
                : "The video goes back into the review queue."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="handoff-note">Note{needsNote ? " (required)" : ""}</Label>
            <Textarea
              id="handoff-note"
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTarget(null)}>
              Cancel
            </Button>
            <Button
              variant={target === "rejected" ? "destructive" : "default"}
              disabled={mutation.isPending || (needsNote && !note.trim())}
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
