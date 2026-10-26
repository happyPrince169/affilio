"use client";

import { useQuery } from "@tanstack/react-query";
import { Film, Plus, Search, TriangleAlert, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { CampaignStatusBadge, PUBLICATION_LABEL } from "@/components/distribution/badges";
import { CampaignFormDialog } from "@/components/distribution/campaign-form-dialog";
import { DistributionTabs } from "@/components/distribution/distribution-tabs";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { publishingQueries } from "@/lib/api/publishing";

export function CampaignsList() {
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const campaigns = useQuery(publishingQueries.campaigns({ q: q || undefined, page_size: 50 }));
  const stats = useQuery(publishingQueries.stats());

  return (
    <div className="space-y-6">
      <PageHeader
        title="Distribution Center"
        description="Plan campaigns, prepare unique content per Page, get approval, schedule, and record what was published. Assisted publishing only: nothing posts automatically in M3A."
        actions={
          can("edit") ? (
            <>
              <Button asChild variant="outline">
                <Link href="/distribution/compose">Open composer</Link>
              </Button>
              <CampaignFormDialog
                trigger={
                  <Button>
                    <Plus /> New campaign
                  </Button>
                }
              />
            </>
          ) : null
        }
      />
      <DistributionTabs />
      {stats.data ? (
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary" className="font-mono">
            {stats.data.campaigns_active} active campaign
            {stats.data.campaigns_active === 1 ? "" : "s"}
          </Badge>
          <Badge variant="outline" className="font-mono">
            {stats.data.by_status.scheduled} scheduled
          </Badge>
          <Badge variant={stats.data.due_now ? "default" : "outline"} className="font-mono">
            {stats.data.due_now} due now
          </Badge>
          <Badge
            variant={stats.data.needs_attention ? "destructive" : "outline"}
            className="font-mono"
          >
            {stats.data.needs_attention} need attention
          </Badge>
          <Badge variant="outline" className="font-mono">
            {stats.data.by_status.published} published
          </Badge>
        </div>
      ) : null}
      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search campaigns…"
          className="pl-8"
          aria-label="Search campaigns"
        />
      </div>
      {campaigns.isPending ? (
        <div className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-36 rounded-xl" />
          <Skeleton className="h-36 rounded-xl" />
        </div>
      ) : campaigns.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load campaigns</AlertTitle>
          <AlertDescription>{campaigns.error.message}</AlertDescription>
        </Alert>
      ) : campaigns.data.total === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-16 text-center">
          <h2 className="text-lg font-semibold">No campaigns yet</h2>
          <p className="max-w-md text-sm text-muted-foreground">
            Create a campaign, add approved final videos and the Pages to publish to, then prepare
            content for each Page in the composer.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {campaigns.data.items.map((c) => {
            const counts = Object.entries(c.publication_counts) as [
              keyof typeof PUBLICATION_LABEL,
              number,
            ][];
            return (
              <Link
                key={c.id}
                href={`/distribution/campaigns/${c.id}`}
                className="space-y-3 rounded-xl border bg-card p-4 shadow-xs transition-colors outline-none hover:border-brand/50 focus-visible:ring-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{c.name}</p>
                    {c.description ? (
                      <p className="line-clamp-2 text-xs text-muted-foreground">{c.description}</p>
                    ) : null}
                  </div>
                  <CampaignStatusBadge status={c.status} />
                </div>
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1">
                    <Film className="size-3" /> {c.media_count} video
                    {c.media_count === 1 ? "" : "s"}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <Users className="size-3" /> {c.target_count} target
                    {c.target_count === 1 ? "" : "s"}
                  </span>
                  {c.starts_on || c.ends_on ? (
                    <span>
                      {c.starts_on ?? "…"} → {c.ends_on ?? "…"}
                    </span>
                  ) : null}
                </div>
                {counts.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {counts.map(([status, n]) => (
                      <Badge key={status} variant="outline" className="text-[10px]">
                        {n} {PUBLICATION_LABEL[status].toLowerCase()}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">No publications yet.</p>
                )}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
