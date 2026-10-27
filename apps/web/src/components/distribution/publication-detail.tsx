"use client";

import type { AffiliateLink, PublicationDetail } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  ClipboardCopy,
  ExternalLink,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import {
  EvidenceBadge,
  PUBLICATION_LABEL,
  PublicationStatusBadge,
} from "@/components/distribution/badges";
import { DistributionTabs } from "@/components/distribution/distribution-tabs";
import {
  CheckWithProviderButton,
  ELIGIBILITY_LABEL,
  MockPublicationBadge,
  UploadPanel,
} from "@/components/distribution/upload-progress";
import { LinkPicker } from "@/components/distribution/link-picker";
import {
  ConfirmPublishedDialog,
  ReconcileDialog,
  ReportProblemDialog,
  ScheduleDialog,
  SimpleActionButton,
} from "@/components/distribution/publication-actions";
import { PageHeader } from "@/components/layout/page-header";
import { MediaThumbnail } from "@/components/media/media-thumbnail";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { authQueries } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { publishingQueries, updatePublicationContent } from "@/lib/api/publishing";
import { formatInZone } from "@/lib/format";

const EDITABLE = new Set([
  "draft",
  "ready_for_review",
  "approved",
  "scheduled",
  "ready_to_publish",
]);

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          toast.error("Clipboard not available; select and copy manually.");
        }
      }}
    >
      {copied ? <Check /> : <ClipboardCopy />} {label}
    </Button>
  );
}

function ContentEditor({ job }: { job: PublicationDetail }) {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(job.title ?? "");
  const [caption, setCaption] = useState(job.caption ?? "");
  const [cta, setCta] = useState(job.cta ?? "");
  const [hashtags, setHashtags] = useState(job.hashtags.join(" "));
  const [notes, setNotes] = useState(job.content_notes ?? "");
  const [links, setLinks] = useState<AffiliateLink[]>(
    job.links.map((l) => ({
      id: l.affiliate_link_id,
      platform: l.platform,
      product_id: null,
      product_name: null,
      label: l.label,
      url: l.url,
      source: "manual",
      status: l.status as AffiliateLink["status"],
      notes: null,
      created_by: null,
      created_at: "",
      updated_at: "",
    })),
  );
  const mutation = useMutation({
    mutationFn: () =>
      updatePublicationContent(job.id, {
        version: job.version,
        title: title.trim() || null,
        caption: caption.trim() || null,
        cta: cta.trim() || null,
        hashtags: hashtags
          .split(/[\s,]+/)
          .map((t) => t.replace(/^#/, ""))
          .filter(Boolean),
        link_ids: links.map((l) => l.id),
        content_notes: notes.trim() || null,
      }),
    onSuccess: (saved) => {
      toast.success(
        saved.status === "draft" && job.status !== "draft"
          ? "Saved. Approval was invalidated; resubmit for review."
          : "Content saved",
      );
      queryClient.setQueryData(publishingQueries.publication(job.id).queryKey, saved);
      void queryClient.invalidateQueries({ queryKey: publishingQueries.all() });
    },
    onError: (error) =>
      toast.error("Could not save", {
        description:
          error instanceof ApiError && error.code === "version_conflict"
            ? "Someone else changed this publication. Reload first."
            : error.message,
      }),
  });
  const editable = can("edit") && EDITABLE.has(job.status);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Content</CardTitle>
        <CardDescription>
          {job.has_approved_snapshot
            ? "Editing approved content moves the publication back to draft and requires a new approval."
            : "Draft content. Submit for review when ready."}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="pub-title">Title (internal)</Label>
          <Input
            id="pub-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={!editable}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="pub-caption">Caption</Label>
          <Textarea
            id="pub-caption"
            rows={6}
            value={caption}
            onChange={(e) => setCaption(e.target.value)}
            disabled={!editable}
          />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="pub-cta">Call to action</Label>
            <Input
              id="pub-cta"
              value={cta}
              onChange={(e) => setCta(e.target.value)}
              disabled={!editable}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Hashtags</Label>
            <Input
              value={hashtags}
              onChange={(e) => setHashtags(e.target.value)}
              disabled={!editable}
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label>Affiliate links</Label>
          {editable ? (
            <LinkPicker value={links} onChange={setLinks} />
          ) : (
            <ol className="space-y-1 text-sm">
              {job.links.map((l) => (
                <li key={l.affiliate_link_id} className="truncate">
                  {l.position + 1}. {l.label} ·{" "}
                  <span className="text-muted-foreground">{l.url}</span>
                </li>
              ))}
              {job.links.length === 0 ? <li className="text-muted-foreground">None</li> : null}
            </ol>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="pub-notes">Internal notes</Label>
          <Input
            id="pub-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={!editable}
          />
        </div>
        {!job.validation.ok ? (
          <Alert variant="destructive">
            <TriangleAlert />
            <AlertTitle>Not ready for review</AlertTitle>
            <AlertDescription>
              <ul className="list-disc pl-4">
                {job.validation.errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}
        {job.validation.warnings.length > 0 ? (
          <ul className="list-disc pl-4 text-xs text-warning">
            {job.validation.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        ) : null}
        {editable ? (
          <div>
            <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
              {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Save content
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function AssistedPanel({ job }: { job: PublicationDetail }) {
  const { can } = useAuth();
  const active =
    job.status === "publishing" ||
    job.status === "ready_to_publish" ||
    job.status === "scheduled" ||
    job.status === "approved";
  const prepared = useQuery(
    publishingQueries.prepared(job.id, active && job.has_approved_snapshot),
  );
  if (!active || !job.has_approved_snapshot) return null;
  return (
    <Card className="border-brand/40">
      <CardHeader>
        <CardTitle className="text-base">Assisted publishing checklist</CardTitle>
        <CardDescription>
          Built from the approved snapshot (content version {job.approved_snapshot?.content_version}
          ). Publish in the platform UI yourself; AFFILIO never posts on your behalf in M3A.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {prepared.isPending ? (
          <Skeleton className="h-32" />
        ) : prepared.isError ? (
          <p className="text-sm text-destructive">{prepared.error.message}</p>
        ) : (
          <>
            <ol className="space-y-1 text-sm">
              {prepared.data.checklist.map((item, i) => (
                <li key={item.key} className="flex gap-2">
                  <span className="w-5 font-mono text-muted-foreground">{i + 1}.</span> {item.label}
                </li>
              ))}
            </ol>
            <div className="flex flex-wrap gap-2">
              <CopyButton text={prepared.data.caption_text} label="Copy caption" />
              {prepared.data.links.length > 0 ? (
                <CopyButton
                  text={prepared.data.links
                    .map((l) => l.url)
                    .filter(Boolean)
                    .join("\n")}
                  label="Copy links"
                />
              ) : null}
              {prepared.data.open_url ? (
                <Button asChild variant="outline" size="sm">
                  <a href={prepared.data.open_url} target="_blank" rel="noreferrer noopener">
                    <ExternalLink /> Open Page
                  </a>
                </Button>
              ) : null}
            </div>
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Approved caption</p>
              <pre className="rounded-md border bg-muted/40 p-3 text-xs whitespace-pre-wrap">
                {prepared.data.caption_text}
              </pre>
            </div>
            <p className="text-xs text-muted-foreground">
              File:{" "}
              <span className="font-mono">
                {prepared.data.media_relative_path ?? prepared.data.media_file_name}
              </span>{" "}
              (finals root on the NAS) · intent{" "}
              <span className="font-mono">{prepared.data.intent_id.slice(0, 8)}</span>
            </p>
            <ul className="list-disc pl-4 text-xs text-muted-foreground">
              {prepared.data.instructions.map((i) => (
                <li key={i}>{i}</li>
              ))}
            </ul>
          </>
        )}
        {can("edit") ? (
          <div className="flex flex-wrap gap-2 border-t pt-3">
            {job.status === "publishing" ? (
              <>
                <ConfirmPublishedDialog job={job} />
                <ReportProblemDialog job={job} />
              </>
            ) : job.allowed_actions.includes("start") ? (
              <SimpleActionButton job={job} action="start" variant="default">
                Start publishing now
              </SimpleActionButton>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ApiPanel({ job }: { job: PublicationDetail }) {
  const { can } = useAuth();
  const upload = job.upload;
  const parked = job.status === "needs_reconciliation" && upload?.phase === "needs_reconciliation";
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          API publishing
          {job.target_is_mock ? <MockPublicationBadge /> : null}
        </CardTitle>
        <CardDescription>
          {job.target_is_mock
            ? "The connected Page is a mock: the publisher worker simulates the upload, processing and publish steps. No Facebook post is created."
            : "The publisher worker uploads the approved final to the connected Page and verifies the result."}
          {job.target_hold_until
            ? ` Page hold (${job.target_hold_reason}) until ${job.target_hold_until}.`
            : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {upload ? (
          <UploadPanel upload={upload} />
        ) : (
          <p className="text-sm text-muted-foreground">
            No API attempt yet.{" "}
            {job.status === "scheduled"
              ? "The publisher starts it when the scheduled time arrives."
              : job.allowed_actions.includes("start")
                ? "Start now or schedule it."
                : ""}
          </p>
        )}
        {job.uploads.length > 1 ? (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">
              Earlier attempts ({job.uploads.length - 1})
            </summary>
            <ol className="mt-2 space-y-2">
              {job.uploads
                .filter((u) => u.id !== upload?.id)
                .map((u) => (
                  <li key={u.id} className="rounded-md border p-2">
                    <UploadPanel upload={u} />
                  </li>
                ))}
            </ol>
          </details>
        ) : null}
        {job.api_eligibility_reason ? (
          <p className="text-xs text-warning">
            {ELIGIBILITY_LABEL[job.api_eligibility_reason] ?? job.api_eligibility_reason}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2 border-t pt-3">
          {can("edit") && job.allowed_actions.includes("start") ? (
            <SimpleActionButton job={job} action="start" variant="default">
              Publish now via API
            </SimpleActionButton>
          ) : null}
          {can("approve") && parked ? <CheckWithProviderButton job={job} /> : null}
        </div>
      </CardContent>
    </Card>
  );
}

function ActionBar({ job }: { job: PublicationDetail }) {
  const allowed = new Set(job.allowed_actions);
  const buttons: React.ReactNode[] = [];
  if (allowed.has("submit"))
    buttons.push(<SimpleActionButton key="submit" job={job} action="submit" variant="default" />);
  if (allowed.has("approve"))
    buttons.push(<SimpleActionButton key="approve" job={job} action="approve" variant="default" />);
  if (allowed.has("return_to_draft"))
    buttons.push(<SimpleActionButton key="rtd" job={job} action="return_to_draft" />);
  if (allowed.has("schedule")) buttons.push(<ScheduleDialog key="schedule" job={job} />);
  if (allowed.has("release") && job.overdue_minutes !== null)
    buttons.push(
      <SimpleActionButton key="release" job={job} action="release" variant="default">
        {job.overdue_review_required ? "Release now (overdue review)" : "Release now"}
      </SimpleActionButton>,
    );
  if (allowed.has("unschedule"))
    buttons.push(<SimpleActionButton key="unschedule" job={job} action="unschedule" />);
  if (allowed.has("revoke_approval"))
    buttons.push(<SimpleActionButton key="revoke" job={job} action="revoke_approval" />);
  if (allowed.has("retry"))
    buttons.push(<SimpleActionButton key="retry" job={job} action="retry" variant="default" />);
  if (allowed.has("revise"))
    buttons.push(<SimpleActionButton key="revise" job={job} action="revise" />);
  if (allowed.has("revalidate") && job.classification_stale)
    buttons.push(
      <SimpleActionButton key="revalidate" job={job} action="revalidate" variant="default" />,
    );
  if (allowed.has("reconcile")) buttons.push(<ReconcileDialog key="reconcile" job={job} />);
  if (allowed.has("cancel"))
    buttons.push(<SimpleActionButton key="cancel" job={job} action="cancel" variant="ghost" />);
  if (buttons.length === 0) return null;
  return <div className="flex flex-wrap gap-2">{buttons}</div>;
}

export function PublicationDetailRoute() {
  const params = useParams<{ id: string }>();
  const { timezone, can } = useAuth();
  const query = useQuery(publishingQueries.publication(params.id));
  const audit = useQuery({
    ...authQueries.audit({ entity_type: "publication_job", entity_id: params.id, limit: 50 }),
    enabled: can("approve"),
  });

  if (query.isPending) return <Skeleton className="h-96 rounded-xl" aria-busy />;
  if (query.isError) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <Alert variant={notFound ? "default" : "destructive"}>
        <TriangleAlert />
        <AlertTitle>{notFound ? "Publication not found" : "Could not load publication"}</AlertTitle>
        <AlertDescription>{query.error.message}</AlertDescription>
      </Alert>
    );
  }
  const job = query.data;
  return (
    <div className="space-y-6">
      <Button asChild variant="ghost" size="sm" className="-ml-2">
        <Link href={`/distribution/campaigns/${job.campaign_id}`}>
          <ArrowLeft /> {job.campaign_name}
        </Link>
      </Button>
      <PageHeader
        title={job.title || job.media_title}
        description={`${job.account_display_name} · ${job.platform} · ${job.account_connection_label}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <PublicationStatusBadge status={job.status} />
            <EvidenceBadge kind={job.evidence_kind} />
            <Badge variant="outline" className="font-mono text-[10px]">
              v{job.version} · content v{job.content_version}
            </Badge>
          </div>
        }
      />
      <DistributionTabs />
      <ActionBar job={job} />
      {job.status === "needs_reconciliation" ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Outcome uncertain</AlertTitle>
          <AlertDescription>
            {job.last_error ?? "The last attempt ended without a clear result."} Nothing will be
            retried automatically. An admin must check the Page and reconcile.
          </AlertDescription>
        </Alert>
      ) : null}
      {job.target_blocked_reason ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Target is no longer eligible</AlertTitle>
          <AlertDescription>
            {job.target_blocked_reason}. Scheduling, starting and retrying are refused until the
            channel rules change or this publication is cancelled. Nothing was changed
            automatically.
          </AlertDescription>
        </Alert>
      ) : null}
      {job.classification_stale ? (
        <Alert>
          <TriangleAlert />
          <AlertTitle>The video&apos;s folder or category changed after approval</AlertTitle>
          <AlertDescription>
            The approved snapshot is untouched and still points at the file path it was approved
            with. Publishing steps are refused until an admin either revalidates the approval
            (“Revalidate approval”) or revokes it.
          </AlertDescription>
        </Alert>
      ) : null}
      {job.overdue_review_required ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Missed schedule needs your decision</AlertTitle>
          <AlertDescription>
            This publication was due {job.overdue_minutes} minutes ago, beyond the grace window, so
            the publisher did not release it automatically. Release it now, reschedule it, or cancel
            it. The decision is recorded in the audit trail.
          </AlertDescription>
        </Alert>
      ) : null}
      {job.status === "published" ? (
        <Alert className={job.target_is_mock ? "border-amber-500/50 bg-amber-500/10" : undefined}>
          <Check />
          <AlertTitle className="flex flex-wrap items-center gap-2">
            {job.target_is_mock ? "Simulated publication" : "Published"} (
            {job.evidence_kind === "api_verified" ? "API verified" : "operator-reported"})
            {job.target_is_mock ? <MockPublicationBadge /> : null}
          </AlertTitle>
          <AlertDescription>
            {job.target_is_mock ? (
              <span className="block text-xs">
                Produced by the mock provider: no Facebook post exists. Reference{" "}
                <span className="font-mono">{job.external_post_url ?? job.external_post_id}</span>
              </span>
            ) : job.external_post_url ? (
              <a
                href={job.external_post_url}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-1 underline"
              >
                {job.external_post_url} <ExternalLink className="size-3" />
              </a>
            ) : null}
            <span className="block text-xs">
              Confirmed {formatInZone(job.confirmed_at, timezone)} by {job.confirmed_by_label}
              {job.external_post_id ? ` · post id ${job.external_post_id}` : ""}
              {job.confirmation_note ? ` · “${job.confirmation_note}”` : ""}
            </span>
          </AlertDescription>
        </Alert>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {job.publish_mode === "api" ? <ApiPanel job={job} /> : <AssistedPanel job={job} />}
          <ContentEditor job={job} />
        </div>
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Video</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <MediaThumbnail
                asset={{
                  thumbnail_url: job.thumbnail_url,
                  thumbnail_status: job.thumbnail_url ? "ready" : "pending",
                  status: "active",
                  title: job.media_title,
                }}
                className="aspect-video rounded-md"
              />
              <Link
                href={`/content/${job.media_asset_id}`}
                className="block truncate font-medium hover:underline"
              >
                {job.media_title}
              </Link>
              <p className="truncate text-xs text-muted-foreground">{job.media_file_name}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Schedule</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm">
              <p>
                <span className="text-muted-foreground">Scheduled:</span>{" "}
                {formatInZone(job.scheduled_at, timezone)}
              </p>
              <p>
                <span className="text-muted-foreground">Published:</span>{" "}
                {formatInZone(job.published_at, timezone)}
              </p>
              <p className="text-xs text-muted-foreground">
                Displayed in {timezone}; stored in UTC.
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Approval</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {job.approved_snapshot ? (
                <>
                  <p>
                    Approved by {job.approved_snapshot.approved_by_label} ·{" "}
                    {formatInZone(job.approved_snapshot.approved_at, timezone)}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Snapshot {job.approved_snapshot.id.slice(0, 8)} · content v
                    {job.approved_snapshot.content_version} · {job.approved_snapshot.links.length}{" "}
                    link(s)
                  </p>
                </>
              ) : (
                <p className="text-muted-foreground">No current approval.</p>
              )}
              {job.snapshots.length > 1 ? (
                <p className="text-xs text-muted-foreground">
                  {job.snapshots.length} snapshots in history (immutable).
                </p>
              ) : null}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Attempts ({job.attempts.length})</CardTitle>
            </CardHeader>
            <CardContent>
              {job.attempts.length === 0 ? (
                <p className="text-sm text-muted-foreground">No attempts yet.</p>
              ) : (
                <ol className="space-y-2 text-xs">
                  {job.attempts.map((a) => (
                    <li key={a.id} className="rounded-md border p-2">
                      <p className="font-medium">
                        #{a.attempt_no} · {a.mode} · {a.adapter} ·{" "}
                        <span className="capitalize">{a.status}</span>
                      </p>
                      <p className="text-muted-foreground">
                        {formatInZone(a.started_at, timezone)} by {a.started_by_label}
                        {a.finished_at ? ` → ${formatInZone(a.finished_at, timezone)}` : ""}
                      </p>
                      {a.error ? <p className="text-destructive">{a.error}</p> : null}
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>
          {can("approve") ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Audit trail</CardTitle>
              </CardHeader>
              <CardContent>
                {audit.data && audit.data.length > 0 ? (
                  <ol className="space-y-1 text-xs">
                    {audit.data.map((e) => (
                      <li key={e.id} className="flex justify-between gap-2">
                        <span className="truncate">
                          <span className="font-mono">{e.action.replace("publication.", "")}</span>{" "}
                          <span className="text-muted-foreground">{e.actor_label}</span>
                        </span>
                        <span className="shrink-0 text-muted-foreground">
                          {formatInZone(e.created_at, timezone)}
                        </span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    {audit.isPending ? "Loading…" : "No events."}
                  </p>
                )}
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        Status: {PUBLICATION_LABEL[job.status]} since{" "}
        {formatInZone(job.status_changed_at, timezone)} · intent{" "}
        <span className="font-mono">{job.intent_id}</span>
      </p>
    </div>
  );
}
