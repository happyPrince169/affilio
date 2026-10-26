"use client";

import type { PublicationDetail } from "@affilio/shared-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api/client";
import {
  confirmPublication,
  failPublication,
  publishingQueries,
  reconcilePublication,
  runSimpleAction,
  schedulePublication,
  type SimpleAction,
} from "@/lib/api/publishing";
import { toLocalInputValue, zonedInputToIso } from "@/lib/format";

const SIMPLE_LABEL: Record<SimpleAction, string> = {
  submit: "Submit for review",
  return_to_draft: "Return to draft",
  approve: "Approve",
  revoke_approval: "Revoke approval",
  unschedule: "Unschedule",
  start: "Start publishing",
  retry: "Retry (explicit)",
  revise: "Revise",
  revalidate: "Revalidate approval",
  cancel: "Cancel publication",
};

function useInvalidate(job: PublicationDetail) {
  const queryClient = useQueryClient();
  return (saved: PublicationDetail) => {
    queryClient.setQueryData(publishingQueries.publication(job.id).queryKey, saved);
    void queryClient.invalidateQueries({ queryKey: publishingQueries.all() });
  };
}

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "version_conflict")
      return "Someone else changed this publication. Reload and try again.";
    const details = error.details as { errors?: string[] } | undefined;
    if (details?.errors?.length) return `${error.message}: ${details.errors.join("; ")}`;
  }
  return error instanceof Error ? error.message : String(error);
}

export function SimpleActionButton({
  job,
  action,
  variant = "outline",
  size = "sm",
  children,
}: {
  job: PublicationDetail;
  action: SimpleAction;
  variant?: "outline" | "default" | "destructive" | "secondary" | "ghost";
  size?: "sm" | "default";
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const done = useInvalidate(job);
  const mutation = useMutation({
    mutationFn: () =>
      runSimpleAction(job.id, action, { version: job.version, note: note.trim() || null }),
    onSuccess: (saved) => {
      toast.success(SIMPLE_LABEL[action]);
      done(saved);
      setOpen(false);
    },
    onError: (error) =>
      toast.error(`${SIMPLE_LABEL[action]} failed`, { description: describe(error) }),
  });
  const needsNote =
    action === "cancel" ||
    action === "revoke_approval" ||
    action === "return_to_draft" ||
    action === "revalidate";
  if (!needsNote) {
    return (
      <Button
        variant={variant}
        size={size}
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
      >
        {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
        {children ?? SIMPLE_LABEL[action]}
      </Button>
    );
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant={variant} size={size}>
          {children ?? SIMPLE_LABEL[action]}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{SIMPLE_LABEL[action]}</DialogTitle>
          <DialogDescription>
            Add a short reason; it is recorded in the audit trail.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Reason"
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Back
          </Button>
          <Button
            variant={action === "cancel" ? "destructive" : "default"}
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Confirm
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ScheduleDialog({ job }: { job: PublicationDetail }) {
  const { timezone } = useAuth();
  const [open, setOpen] = useState(false);
  const [local, setLocal] = useState(() =>
    toLocalInputValue(
      job.scheduled_at ? new Date(job.scheduled_at) : new Date(Date.now() + 3_600_000),
      timezone,
    ),
  );
  const [note, setNote] = useState("");
  const [conflict, setConflict] = useState<string | null>(null);
  const done = useInvalidate(job);
  const mutation = useMutation({
    mutationFn: (allowConflict: boolean) => {
      const iso = zonedInputToIso(local, timezone);
      if (!iso) throw new Error("Pick a valid date and time");
      return schedulePublication(job.id, {
        version: job.version,
        scheduled_at: iso,
        note: note.trim() || null,
        allow_conflict: allowConflict,
      });
    },
    onSuccess: (saved) => {
      toast.success("Scheduled");
      done(saved);
      setOpen(false);
      setConflict(null);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "schedule_conflict") {
        setConflict(error.message);
        return;
      }
      toast.error("Could not schedule", { description: describe(error) });
    },
  });
  const iso = zonedInputToIso(local, timezone);
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        setConflict(null);
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm">{job.status === "scheduled" ? "Reschedule" : "Schedule"}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Schedule publication</DialogTitle>
          <DialogDescription>
            Time is entered in {timezone} and stored in UTC. Scheduling never publishes by itself:
            the item becomes due in the queue for assisted publishing.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="sched">When ({timezone})</Label>
            <Input
              id="sched"
              type="datetime-local"
              value={local}
              onChange={(e) => setLocal(e.target.value)}
            />
            {iso ? (
              <p className="text-xs text-muted-foreground">UTC: {iso.replace(".000Z", "Z")}</p>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="sched-note">Note</Label>
            <Input id="sched-note" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          {conflict ? (
            <Alert>
              <AlertTitle>Scheduling conflict</AlertTitle>
              <AlertDescription>{conflict}</AlertDescription>
            </Alert>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Back
          </Button>
          {conflict ? (
            <Button
              variant="secondary"
              onClick={() => mutation.mutate(true)}
              disabled={mutation.isPending}
            >
              Schedule anyway
            </Button>
          ) : null}
          <Button onClick={() => mutation.mutate(false)} disabled={mutation.isPending || !iso}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Schedule
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ConfirmPublishedDialog({ job }: { job: PublicationDetail }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [postId, setPostId] = useState("");
  const [note, setNote] = useState("");
  const done = useInvalidate(job);
  const mutation = useMutation({
    mutationFn: () =>
      confirmPublication(job.id, {
        version: job.version,
        external_post_url: url.trim(),
        external_post_id: postId.trim() || null,
        note: note.trim() || null,
      }),
    onSuccess: (saved) => {
      toast.success("Recorded as published (operator-reported)");
      done(saved);
      setOpen(false);
    },
    onError: (error) => toast.error("Could not record evidence", { description: describe(error) }),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">I published it</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Record the published post</DialogTitle>
          <DialogDescription>
            Paste the URL of the live post. This is operator-reported evidence, labelled as such;
            the same post cannot be used to confirm two publications.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="ev-url">Post URL</Label>
            <Input
              id="ev-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.facebook.com/reel/…"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ev-id">Post id (optional, derived from the URL when possible)</Label>
            <Input id="ev-id" value={postId} onChange={(e) => setPostId(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ev-note">Note</Label>
            <Textarea
              id="ev-note"
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Back
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending || !url.trim()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Confirm
            published
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ReportProblemDialog({ job }: { job: PublicationDetail }) {
  const [open, setOpen] = useState(false);
  const [outcome, setOutcome] = useState<"failed" | "ambiguous">("failed");
  const [reason, setReason] = useState("");
  const done = useInvalidate(job);
  const mutation = useMutation({
    mutationFn: () =>
      failPublication(job.id, { version: job.version, reason: reason.trim(), outcome }),
    onSuccess: (saved) => {
      toast.success(outcome === "failed" ? "Marked as failed" : "Marked for reconciliation");
      done(saved);
      setOpen(false);
    },
    onError: (error) =>
      toast.error("Could not record the problem", { description: describe(error) }),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          It did not work
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Report a problem</DialogTitle>
          <DialogDescription>
            If you are not sure whether the post went live, choose “uncertain”. Uncertain outcomes
            are never retried automatically; an admin reconciles them after checking the Page.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="space-y-1.5">
            <Label>Outcome</Label>
            <Select value={outcome} onValueChange={(v) => setOutcome(v as "failed" | "ambiguous")}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="failed">Failed – nothing was published</SelectItem>
                <SelectItem value="ambiguous">Uncertain – it may have been published</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="fail-reason">What happened</Label>
            <Textarea
              id="fail-reason"
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Back
          </Button>
          <Button
            variant="destructive"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending || !reason.trim()}
          >
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Record
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ReconcileDialog({ job }: { job: PublicationDetail }) {
  const [open, setOpen] = useState(false);
  const [outcome, setOutcome] = useState<"published" | "not_published" | "still_unknown">(
    "still_unknown",
  );
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const done = useInvalidate(job);
  const mutation = useMutation({
    mutationFn: () =>
      reconcilePublication(job.id, {
        version: job.version,
        outcome,
        external_post_url: url.trim() || null,
        note: note.trim() || null,
      }),
    onSuccess: (saved) => {
      toast.success("Reconciliation recorded");
      done(saved);
      setOpen(false);
    },
    onError: (error) => toast.error("Could not reconcile", { description: describe(error) }),
  });
  const valid =
    outcome === "published"
      ? Boolean(url.trim())
      : outcome === "not_published"
        ? Boolean(note.trim())
        : true;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">Reconcile</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reconcile an uncertain outcome</DialogTitle>
          <DialogDescription>
            Check the Page yourself. “Published” requires the post URL; “not published” requires a
            note. Nothing is retried here.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="space-y-1.5">
            <Label>Finding</Label>
            <Select value={outcome} onValueChange={(v) => setOutcome(v as typeof outcome)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="published">The post is live</SelectItem>
                <SelectItem value="not_published">Nothing was published</SelectItem>
                <SelectItem value="still_unknown">Still unknown – keep flagged</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {outcome === "published" ? (
            <div className="space-y-1.5">
              <Label htmlFor="rec-url">Post URL</Label>
              <Input id="rec-url" value={url} onChange={(e) => setUrl(e.target.value)} />
            </div>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor="rec-note">Note</Label>
            <Textarea
              id="rec-note"
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Back
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending || !valid}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Record finding
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
