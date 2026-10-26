"use client";

import type { MediaAssetSummary, SocialAccount } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Film, Pencil, Plus, TriangleAlert, Users, X } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { CampaignStatusBadge } from "@/components/distribution/badges";
import { CampaignFormDialog } from "@/components/distribution/campaign-form-dialog";
import { DistributionTabs } from "@/components/distribution/distribution-tabs";
import { FinalPicker } from "@/components/distribution/final-picker";
import { PublicationRow } from "@/components/distribution/publication-row";
import { PageHeader } from "@/components/layout/page-header";
import { MediaThumbnail } from "@/components/media/media-thumbnail";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { accountQueries } from "@/lib/api/accounts";
import { ApiError } from "@/lib/api/client";
import {
  addCampaignMedia,
  addCampaignTargets,
  publishingQueries,
  removeCampaignMedia,
  removeCampaignTarget,
} from "@/lib/api/publishing";
import { formatDuration } from "@/lib/format";

function AddMediaDialog({ campaignId, existing }: { campaignId: string; existing: Set<string> }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Map<string, MediaAssetSummary>>(new Map());
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => addCampaignMedia(campaignId, [...selected.keys()]),
    onSuccess: () => {
      toast.success("Videos added");
      void queryClient.invalidateQueries({ queryKey: publishingQueries.all() });
      setSelected(new Map());
      setOpen(false);
    },
    onError: (error) => toast.error("Could not add videos", { description: error.message }),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          <Plus /> Add videos
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add approved finals</DialogTitle>
          <DialogDescription>
            Only finals approved in the handoff review are listed.
          </DialogDescription>
        </DialogHeader>
        <FinalPicker
          selected={new Set([...selected.keys(), ...existing])}
          onToggle={(asset) => {
            if (existing.has(asset.id)) return;
            const next = new Map(selected);
            if (next.has(asset.id)) next.delete(asset.id);
            else next.set(asset.id, asset);
            setSelected(next);
          }}
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={selected.size === 0 || mutation.isPending}
          >
            Add {selected.size || ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AddTargetsDialog({ campaignId, existing }: { campaignId: string; existing: Set<string> }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const accounts = useQuery(accountQueries.list({ include_disabled: false }));
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => addCampaignTargets(campaignId, [...selected]),
    onSuccess: () => {
      toast.success("Targets added");
      void queryClient.invalidateQueries({ queryKey: publishingQueries.all() });
      setSelected(new Set());
      setOpen(false);
    },
    onError: (error) => toast.error("Could not add targets", { description: error.message }),
  });
  const candidates = (accounts.data ?? []).filter((a: SocialAccount) => !existing.has(a.id));
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          <Plus /> Add targets
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add target accounts</DialogTitle>
          <DialogDescription>
            Registered accounts. All are unverified until OAuth (M3B); publishing stays assisted.
          </DialogDescription>
        </DialogHeader>
        {accounts.isPending ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : candidates.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No more accounts to add. Register Pages under Accounts.
          </p>
        ) : (
          <ul className="max-h-72 divide-y overflow-auto rounded-md border">
            {candidates.map((a) => (
              <li key={a.id}>
                <label className="flex cursor-pointer items-center gap-3 px-3 py-2 text-sm">
                  <input
                    type="checkbox"
                    checked={selected.has(a.id)}
                    onChange={(e) => {
                      const next = new Set(selected);
                      if (e.target.checked) next.add(a.id);
                      else next.delete(a.id);
                      setSelected(next);
                    }}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{a.display_name}</span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {a.platform} · {a.connection_label}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={selected.size === 0 || mutation.isPending}
          >
            Add {selected.size || ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function CampaignDetailRoute() {
  const params = useParams<{ id: string }>();
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const query = useQuery(publishingQueries.campaign(params.id));
  const removeMedia = useMutation({
    mutationFn: (assetId: string) => removeCampaignMedia(params.id, assetId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: publishingQueries.all() }),
    onError: (error) => toast.error("Could not remove video", { description: error.message }),
  });
  const removeTarget = useMutation({
    mutationFn: (accountId: string) => removeCampaignTarget(params.id, accountId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: publishingQueries.all() }),
    onError: (error) => toast.error("Could not remove target", { description: error.message }),
  });

  if (query.isPending) return <Skeleton className="h-96 rounded-xl" aria-busy />;
  if (query.isError) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <Alert variant={notFound ? "default" : "destructive"}>
        <TriangleAlert />
        <AlertTitle>{notFound ? "Campaign not found" : "Could not load campaign"}</AlertTitle>
        <AlertDescription>{query.error.message}</AlertDescription>
      </Alert>
    );
  }
  const campaign = query.data;
  const mediaIds = new Set(campaign.media.map((m) => m.media_asset_id));
  const targetIds = new Set(campaign.targets.map((t) => t.social_account_id));

  return (
    <div className="space-y-6">
      <Button asChild variant="ghost" size="sm" className="-ml-2">
        <Link href="/distribution/campaigns">
          <ArrowLeft /> Campaigns
        </Link>
      </Button>
      <PageHeader
        title={campaign.name}
        description={campaign.description ?? undefined}
        actions={
          <>
            <CampaignStatusBadge status={campaign.status} />
            {can("edit") ? (
              <>
                <CampaignFormDialog
                  campaign={campaign}
                  trigger={
                    <Button variant="outline" size="sm">
                      <Pencil /> Edit
                    </Button>
                  }
                />
                <Button asChild size="sm">
                  <Link href={`/distribution/compose?campaign=${campaign.id}`}>Compose</Link>
                </Button>
              </>
            ) : null}
          </>
        }
      />
      <DistributionTabs />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="inline-flex items-center gap-2 text-base">
              <Film className="size-4" /> Approved finals ({campaign.media.length})
            </CardTitle>
            {can("edit") ? <AddMediaDialog campaignId={campaign.id} existing={mediaIds} /> : null}
          </CardHeader>
          <CardContent>
            {campaign.media.length === 0 ? (
              <p className="text-sm text-muted-foreground">No videos yet.</p>
            ) : (
              <ul className="space-y-2">
                {campaign.media.map((m) => (
                  <li key={m.media_asset_id} className="flex items-center gap-3 text-sm">
                    <MediaThumbnail
                      asset={{
                        thumbnail_url: m.thumbnail_url,
                        thumbnail_status: m.thumbnail_url ? "ready" : "pending",
                        status: "active",
                        title: m.title,
                      }}
                      className="h-12 w-20 shrink-0 rounded-md"
                    />
                    <div className="min-w-0 flex-1">
                      <Link
                        href={`/content/${m.media_asset_id}`}
                        className="block truncate font-medium hover:underline"
                      >
                        {m.title}
                      </Link>
                      <p className="truncate text-xs text-muted-foreground">
                        {m.file_name} · {formatDuration(m.duration_seconds)} ·{" "}
                        <Badge variant="outline" className="text-[10px]">
                          {m.handoff_status ?? "?"}
                        </Badge>
                      </p>
                    </div>
                    {can("edit") ? (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Remove video"
                        onClick={() => removeMedia.mutate(m.media_asset_id)}
                      >
                        <X />
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="inline-flex items-center gap-2 text-base">
              <Users className="size-4" /> Targets ({campaign.targets.length})
            </CardTitle>
            {can("edit") ? (
              <AddTargetsDialog campaignId={campaign.id} existing={targetIds} />
            ) : null}
          </CardHeader>
          <CardContent>
            {campaign.targets.length === 0 ? (
              <p className="text-sm text-muted-foreground">No target accounts yet.</p>
            ) : (
              <ul className="space-y-2">
                {campaign.targets.map((t) => (
                  <li key={t.social_account_id} className="flex items-center gap-3 text-sm">
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{t.display_name}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {t.platform}
                        {t.handle ? ` · @${t.handle}` : ""} · {t.connection_label}
                      </p>
                    </div>
                    {can("edit") ? (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Remove target"
                        onClick={() => removeTarget.mutate(t.social_account_id)}
                      >
                        <X />
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
      <section className="space-y-3">
        <h2 className="text-base font-semibold">Publications ({campaign.publications.length})</h2>
        {campaign.publications.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            None yet. Use the composer to prepare unique content for each target.
          </p>
        ) : (
          <div className="grid gap-2">
            {campaign.publications.map((p) => (
              <PublicationRow key={p.id} item={p} showCampaign={false} />
            ))}
          </div>
        )}
      </section>
      {campaign.notes ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Notes</CardTitle>
          </CardHeader>
          <CardContent className="text-sm whitespace-pre-wrap">{campaign.notes}</CardContent>
        </Card>
      ) : null}
    </div>
  );
}
