"use client";

import type { AffiliateLink, MediaAssetSummary, SocialAccount } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Send, TriangleAlert } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { DistributionTabs } from "@/components/distribution/distribution-tabs";
import { FinalPicker } from "@/components/distribution/final-picker";
import { LinkPicker } from "@/components/distribution/link-picker";
import { RecommendationPicker } from "@/components/distribution/recommendation-picker";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { accountQueries } from "@/lib/api/accounts";
import { createPublicationsBatch, publishingQueries } from "@/lib/api/publishing";

interface TargetDraft {
  title: string;
  caption: string;
  cta: string;
  hashtags: string;
  links: AffiliateLink[];
  notes: string;
}

const emptyDraft = (): TargetDraft => ({
  title: "",
  caption: "",
  cta: "",
  hashtags: "",
  links: [],
  notes: "",
});
const splitTags = (raw: string) =>
  raw
    .split(/[\s,]+/)
    .map((t) => t.replace(/^#/, "").trim())
    .filter(Boolean);

function TargetEditor({
  account,
  draft,
  onChange,
  onCopyFrom,
  others,
}: {
  account: SocialAccount;
  draft: TargetDraft;
  onChange: (next: TargetDraft) => void;
  onCopyFrom: (accountId: string) => void;
  others: SocialAccount[];
}) {
  const caption = draft.caption.trim();
  const composed = [
    caption,
    draft.cta.trim(),
    ...draft.links.map((l) => `${l.label}: ${l.url}`),
    splitTags(draft.hashtags)
      .map((t) => `#${t}`)
      .join(" "),
  ]
    .filter(Boolean)
    .join("\n\n");
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="text-base">
          {account.display_name}{" "}
          <span className="text-xs font-normal text-muted-foreground">
            {account.platform} · {account.connection_label}
          </span>
        </CardTitle>
        {others.length > 0 ? (
          <Select onValueChange={onCopyFrom}>
            <SelectTrigger size="sm" className="w-44">
              <SelectValue placeholder="Copy content from…" />
            </SelectTrigger>
            <SelectContent>
              {others.map((o) => (
                <SelectItem key={o.id} value={o.id}>
                  {o.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
      </CardHeader>
      <CardContent className="grid gap-3 lg:grid-cols-2">
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor={`title-${account.id}`}>Title (internal)</Label>
            <Input
              id={`title-${account.id}`}
              value={draft.title}
              onChange={(e) => onChange({ ...draft, title: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`caption-${account.id}`}>
              Caption <span className="text-xs text-muted-foreground">({caption.length}/2200)</span>
            </Label>
            <Textarea
              id={`caption-${account.id}`}
              rows={5}
              value={draft.caption}
              onChange={(e) => onChange({ ...draft, caption: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor={`cta-${account.id}`}>Call to action</Label>
              <Input
                id={`cta-${account.id}`}
                value={draft.cta}
                onChange={(e) => onChange({ ...draft, cta: e.target.value })}
                placeholder="Shop the link in the first comment"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`tags-${account.id}`}>Hashtags</Label>
              <Input
                id={`tags-${account.id}`}
                value={draft.hashtags}
                onChange={(e) => onChange({ ...draft, hashtags: e.target.value })}
                placeholder="deal shopee reels"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>Affiliate links (ordered)</Label>
            <LinkPicker value={draft.links} onChange={(links) => onChange({ ...draft, links })} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`notes-${account.id}`}>Internal notes</Label>
            <Input
              id={`notes-${account.id}`}
              value={draft.notes}
              onChange={(e) => onChange({ ...draft, notes: e.target.value })}
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label>Preview (what the operator will paste)</Label>
          <pre className="min-h-40 rounded-md border bg-muted/40 p-3 text-xs whitespace-pre-wrap">
            {composed || "—"}
          </pre>
          {account.platform === "facebook" ? (
            <p className="text-xs text-muted-foreground">
              Reels captions do not render links as clickable; the checklist asks to add links in
              the first comment as well.
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

export function Composer() {
  const search = useSearchParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [campaignId, setCampaignId] = useState(search.get("campaign") ?? "");
  const [asset, setAsset] = useState<MediaAssetSummary | null>(null);
  const [targets, setTargets] = useState<Set<string>>(new Set());
  const [drafts, setDrafts] = useState<Record<string, TargetDraft>>({});
  const [batchKey] = useState(() => crypto.randomUUID());

  const campaigns = useQuery(publishingQueries.campaigns({ page_size: 100 }));
  const accounts = useQuery(accountQueries.list({ include_disabled: false }));

  const accountList = useMemo(() => accounts.data ?? [], [accounts.data]);
  const openCampaigns = (campaigns.data?.items ?? []).filter(
    (c) => c.status === "draft" || c.status === "active",
  );

  const mutation = useMutation({
    mutationFn: () =>
      createPublicationsBatch({
        campaign_id: campaignId,
        media_asset_id: asset!.id,
        items: [...targets].map((accountId) => {
          const d = drafts[accountId] ?? emptyDraft();
          return {
            social_account_id: accountId,
            title: d.title.trim() || null,
            caption: d.caption.trim() || null,
            cta: d.cta.trim() || null,
            hashtags: splitTags(d.hashtags),
            link_ids: d.links.map((l) => l.id),
            content_notes: d.notes.trim() || null,
            idempotency_key: `${batchKey}:${accountId}`,
          };
        }),
      }),
    onSuccess: (result) => {
      const n = result.created.length;
      toast.success(`${n} publication${n === 1 ? "" : "s"} created as drafts`, {
        description: result.reused.length
          ? `${result.reused.length} already existed and were reused.`
          : undefined,
      });
      void queryClient.invalidateQueries({ queryKey: publishingQueries.all() });
      router.push(`/distribution/campaigns/${campaignId}`);
    },
    onError: (error) =>
      toast.error("Could not create publications", { description: error.message }),
  });

  const toggleTarget = (id: string) => {
    const next = new Set(targets);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setTargets(next);
    if (!drafts[id]) setDrafts({ ...drafts, [id]: emptyDraft() });
  };
  const ready =
    campaignId &&
    asset &&
    targets.size > 0 &&
    [...targets].every((t) => (drafts[t]?.caption ?? "").trim());

  return (
    <div className="space-y-6">
      <PageHeader
        title="Composer"
        description="One approved final video, unique caption, links and hashtags per Page. Publications are created as drafts; approval, scheduling and assisted publishing happen on each publication."
      />
      <DistributionTabs />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">1 · Campaign</CardTitle>
          </CardHeader>
          <CardContent>
            {campaigns.isError ? (
              <p className="text-sm text-destructive">{campaigns.error.message}</p>
            ) : (
              <Select value={campaignId} onValueChange={setCampaignId}>
                <SelectTrigger>
                  <SelectValue placeholder="Choose a campaign" />
                </SelectTrigger>
                <SelectContent>
                  {openCampaigns.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {campaigns.data && openCampaigns.length === 0 ? (
              <p className="mt-2 text-xs text-muted-foreground">
                No open campaigns. Create one first.
              </p>
            ) : null}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">2 · Approved final</CardTitle>
          </CardHeader>
          <CardContent>
            <FinalPicker
              single
              selected={new Set(asset ? [asset.id] : [])}
              onToggle={(a) => setAsset(asset?.id === a.id ? null : a)}
            />
          </CardContent>
        </Card>
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle className="text-base">3 · Target Pages</CardTitle>
            <CardDescription>
              Rule-based recommendations from the video&apos;s category and each Page&apos;s channel
              profile. Scores are routing relevance, not performance predictions.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {accounts.isPending ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : accountList.length === 0 ? (
              <p className="text-sm text-muted-foreground">Register Pages under Accounts first.</p>
            ) : (
              <RecommendationPicker
                mediaAssetId={asset?.id ?? null}
                campaignId={campaignId || undefined}
                selected={targets}
                onToggle={toggleTarget}
              />
            )}
          </CardContent>
        </Card>
      </div>
      {targets.size > 0 ? (
        <div className="space-y-4">
          <h2 className="text-base font-semibold">4 · Content per Page</h2>
          {[...targets].map((id) => {
            const account = accountList.find((a) => a.id === id);
            if (!account) return null;
            return (
              <TargetEditor
                key={id}
                account={account}
                draft={drafts[id] ?? emptyDraft()}
                onChange={(next) => setDrafts({ ...drafts, [id]: next })}
                others={accountList.filter((a) => a.id !== id && targets.has(a.id))}
                onCopyFrom={(from) =>
                  setDrafts({ ...drafts, [id]: { ...(drafts[from] ?? emptyDraft()) } })
                }
              />
            );
          })}
        </div>
      ) : null}
      {!ready && targets.size > 0 ? (
        <Alert>
          <TriangleAlert />
          <AlertTitle>Each target needs a caption</AlertTitle>
          <AlertDescription>
            Captions can differ per Page; keep them unique to avoid duplicate content.
          </AlertDescription>
        </Alert>
      ) : null}
      <div className="flex justify-end">
        <Button onClick={() => mutation.mutate()} disabled={!ready || mutation.isPending}>
          {mutation.isPending ? <LoaderCircle className="animate-spin" /> : <Send />} Create{" "}
          {targets.size || ""} draft
          {targets.size === 1 ? "" : "s"}
        </Button>
      </div>
    </div>
  );
}
