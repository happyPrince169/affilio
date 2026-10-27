"use client";

import type { PublicationStatus } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, RefreshCw, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { PUBLICATION_LABEL } from "@/components/distribution/badges";
import { DistributionTabs } from "@/components/distribution/distribution-tabs";
import { PublicationRow } from "@/components/distribution/publication-row";
import { EngineStatusCard } from "@/components/distribution/upload-progress";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { publishingQueries, releaseDue } from "@/lib/api/publishing";

const GROUPS: { status: PublicationStatus; hint: string }[] = [
  {
    status: "ready_to_publish",
    hint: "Due now. Connected Pages are picked up by the publisher; assisted ones need the checklist.",
  },
  {
    status: "publishing",
    hint: "API attempts show their upload phase; assisted attempts wait for the operator's result.",
  },
  {
    status: "needs_reconciliation",
    hint: "Outcome uncertain. An admin checks with the provider (never re-sends) or reconciles manually.",
  },
  { status: "scheduled", hint: "Waiting for their time. Release moves due items up." },
  { status: "approved", hint: "Approved, not yet scheduled." },
  { status: "failed", hint: "Failed attempts. Retry explicitly or revise the content." },
];

export function PublishingQueue() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const queue = useQuery(publishingQueries.queue());
  const release = useMutation({
    mutationFn: releaseDue,
    onSuccess: (result) => {
      toast.success(
        result.released ? `${result.released} publication(s) ready to publish` : "Nothing due yet",
      );
      void queryClient.invalidateQueries({ queryKey: publishingQueries.all() });
    },
    onError: (error) => toast.error("Could not release due items", { description: error.message }),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Publishing queue"
        description="Everything approved or in flight, ordered by urgency. Connected Pages publish through the engine; assisted targets are published by you and confirmed with the post URL."
        actions={
          can("edit") ? (
            <Button variant="outline" onClick={() => release.mutate()} disabled={release.isPending}>
              {release.isPending ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}{" "}
              Release due
            </Button>
          ) : null
        }
      />
      <DistributionTabs />
      <EngineStatusCard />
      {queue.isPending ? (
        <Skeleton className="h-64 rounded-xl" />
      ) : queue.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load queue</AlertTitle>
          <AlertDescription>{queue.error.message}</AlertDescription>
        </Alert>
      ) : queue.data.length === 0 ? (
        <p className="rounded-xl border border-dashed px-6 py-16 text-center text-sm text-muted-foreground">
          The queue is empty. Approve and schedule publications to see them here.
        </p>
      ) : (
        GROUPS.map(({ status, hint }) => {
          const items = queue.data.filter((i) => i.status === status);
          if (items.length === 0) return null;
          return (
            <section key={status} className="space-y-2">
              <div>
                <h2 className="text-base font-semibold">
                  {PUBLICATION_LABEL[status]}{" "}
                  <span className="text-sm font-normal text-muted-foreground">
                    ({items.length})
                  </span>
                </h2>
                <p className="text-xs text-muted-foreground">{hint}</p>
              </div>
              <div className="grid gap-2">
                {items.map((item) => (
                  <PublicationRow key={item.id} item={item} />
                ))}
              </div>
            </section>
          );
        })
      )}
    </div>
  );
}
