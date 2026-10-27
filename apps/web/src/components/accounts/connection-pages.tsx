"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FlaskConical, LoaderCircle, ShieldAlert, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { accountQueries, linkConnectionPages } from "@/lib/api/accounts";

/** Page selection after a successful Meta authorization: pick which discovered Pages to connect. */
export function ConnectionPagesRoute() {
  const params = useParams<{ connectionId: string }>();
  const connectionId = params.connectionId;
  const router = useRouter();
  const queryClient = useQueryClient();
  const connection = useQuery(accountQueries.connection(connectionId));
  const pages = useQuery(accountQueries.connectionPages(connectionId));
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const link = useMutation({
    mutationFn: () => linkConnectionPages(connectionId, { page_ids: [...selected] }),
    onSuccess: (accounts) => {
      toast.success(`${accounts.length} Page(s) connected`);
      void queryClient.invalidateQueries({ queryKey: accountQueries.all() });
      router.push("/accounts");
    },
    onError: (error) => toast.error("Could not connect the Pages", { description: error.message }),
  });

  if (connection.isPending || pages.isPending) {
    return <Skeleton className="h-64 rounded-xl" />;
  }
  if (connection.isError || pages.isError) {
    const error = connection.error ?? pages.error;
    return (
      <Alert variant="destructive">
        <TriangleAlert />
        <AlertTitle>Could not load this authorization</AlertTitle>
        <AlertDescription>{error?.message}</AlertDescription>
      </Alert>
    );
  }
  const conn = connection.data;
  const inactive = conn.status !== "active";
  const toggle = (id: string, on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Choose the Pages to connect"
        description={`Pages that ${conn.external_user_name ?? "the authorizing user"} manages. Only Pages where that person can create content are selectable.`}
        actions={
          <Button asChild variant="ghost" size="sm">
            <Link href="/accounts">
              <ArrowLeft /> Back to accounts
            </Link>
          </Button>
        }
      />
      {conn.is_mock ? (
        <Alert className="border-amber-500/50 bg-amber-500/10">
          <FlaskConical />
          <AlertTitle>Mock authorization</AlertTitle>
          <AlertDescription>
            These Pages come from the deterministic mock provider. They are labelled MOCK everywhere
            and are never real Facebook Pages.
          </AlertDescription>
        </Alert>
      ) : null}
      {conn.missing_scopes.length ? (
        <Alert variant="destructive">
          <ShieldAlert />
          <AlertTitle>Permissions missing: {conn.missing_scopes.join(", ")}</AlertTitle>
          <AlertDescription>
            Pages can be connected, but API publishing stays unavailable until the person reconnects
            and grants every requested permission.
          </AlertDescription>
        </Alert>
      ) : null}
      {inactive ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>This authorization is {conn.status}</AlertTitle>
          <AlertDescription>Start a new connection from the accounts page.</AlertDescription>
        </Alert>
      ) : null}
      {pages.data.length === 0 ? (
        <div className="rounded-xl border border-dashed px-6 py-12 text-center text-sm text-muted-foreground">
          The provider listed no Pages with a role for this user. Nothing was connected.
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {pages.data.map((page) => {
            const disabled = inactive || !page.has_publish_task;
            const checked = selected.has(page.id);
            return (
              <Card key={page.id} className={`py-4 ${disabled ? "opacity-70" : ""}`}>
                <CardContent className="flex items-start gap-3">
                  <Checkbox
                    id={`page-${page.id}`}
                    checked={checked}
                    disabled={disabled}
                    onCheckedChange={(value) => toggle(page.id, value === true)}
                    aria-label={`Connect ${page.name}`}
                  />
                  <label htmlFor={`page-${page.id}`} className="min-w-0 flex-1 cursor-pointer">
                    <p className="flex flex-wrap items-center gap-2 font-medium">
                      {page.name}
                      {conn.is_mock ? (
                        <Badge className="bg-amber-500 text-black hover:bg-amber-500">MOCK</Badge>
                      ) : null}
                      {page.linked_connected ? (
                        <Badge className="text-success-foreground bg-success">Connected</Badge>
                      ) : page.linked_account_id ? (
                        <Badge variant="secondary">
                          Registered ({page.linked_source}) · will be upgraded
                        </Badge>
                      ) : null}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      id {page.id}
                      {page.category ? ` · ${page.category}` : ""}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Tasks: {page.tasks.join(", ") || "none"}
                    </p>
                    {!page.has_publish_task ? (
                      <p className="mt-1 text-xs text-destructive">
                        Cannot connect: the authorizing user lacks CREATE_CONTENT on this Page.
                      </p>
                    ) : !page.can_publish ? (
                      <p className="mt-1 text-xs text-amber-600">
                        Connects, but publishing is blocked by missing permissions.
                      </p>
                    ) : null}
                  </label>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button asChild variant="outline">
          <Link href="/accounts">Later</Link>
        </Button>
        <Button
          onClick={() => link.mutate()}
          disabled={inactive || selected.size === 0 || link.isPending}
        >
          {link.isPending ? <LoaderCircle className="animate-spin" /> : null}
          Connect {selected.size || ""} selected
        </Button>
      </div>
    </div>
  );
}
