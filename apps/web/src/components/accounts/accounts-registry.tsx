"use client";

import type { SocialAccount } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Plus, ShieldAlert, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import {
  ChannelProfileDialog,
  ChannelProfileSummary,
} from "@/components/accounts/channel-profile-dialog";
import { RegisterAccountDialog } from "@/components/accounts/register-account-dialog";
import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { accountQueries, changeAccountStatus } from "@/lib/api/accounts";
import { formatDateTime } from "@/lib/format";

export function ConnectionBadge({ account }: { account: SocialAccount }) {
  if (account.is_verified && account.status === "connected")
    return <Badge className="text-success-foreground bg-success">Connected</Badge>;
  if (account.status === "disabled") return <Badge variant="outline">Disabled</Badge>;
  return (
    <Badge variant="secondary" className="gap-1">
      <ShieldAlert className="size-3" /> Unverified · not connected
    </Badge>
  );
}

function AccountCard({ account }: { account: SocialAccount }) {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: () =>
      changeAccountStatus(account.id, {
        status: account.status === "disabled" ? "disconnected" : "disabled",
      }),
    onSuccess: (saved) => {
      toast.success(saved.status === "disabled" ? "Account disabled" : "Account re-enabled");
      void queryClient.invalidateQueries({ queryKey: accountQueries.all() });
    },
    onError: (error) => toast.error("Could not change status", { description: error.message }),
  });
  const caps = account.capabilities as { api_publishing?: boolean; assisted_publishing?: boolean };

  return (
    <Card className="gap-3 py-4">
      <CardContent className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate font-medium">{account.display_name}</p>
            <p className="truncate text-xs text-muted-foreground">
              {account.platform} · {account.account_type}
              {account.handle ? ` · @${account.handle}` : ""}
              {account.external_account_id ? ` · id ${account.external_account_id}` : ""}
            </p>
          </div>
          <ConnectionBadge account={account} />
        </div>
        <div className="flex flex-wrap gap-1.5 text-[11px]">
          <Badge variant="outline">source: {account.source}</Badge>
          <Badge variant="outline">
            assisted publishing: {caps.assisted_publishing ? "yes" : "no"}
          </Badge>
          <Badge variant="outline">
            API publishing: {caps.api_publishing ? "yes" : "no (M3B)"}
          </Badge>
        </div>
        <ChannelProfileSummary accountId={account.id} />
        {account.notes ? (
          <p className="text-xs whitespace-pre-wrap text-muted-foreground">{account.notes}</p>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>
            Registered {formatDateTime(account.created_at)}
            {account.registered_by ? ` by ${account.registered_by}` : ""}
          </span>
          <div className="flex items-center gap-1">
            {account.profile_url ? (
              <Button asChild variant="ghost" size="sm">
                <a href={account.profile_url} target="_blank" rel="noreferrer noopener">
                  <ExternalLink /> Open
                </a>
              </Button>
            ) : null}
            {can("manage_accounts") ? (
              <>
                <ChannelProfileDialog
                  account={account}
                  trigger={
                    <Button variant="outline" size="sm">
                      Routing
                    </Button>
                  }
                />
                <RegisterAccountDialog
                  account={account}
                  trigger={
                    <Button variant="outline" size="sm">
                      Edit
                    </Button>
                  }
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => toggle.mutate()}
                  disabled={toggle.isPending}
                >
                  {account.status === "disabled" ? "Re-enable" : "Disable"}
                </Button>
              </>
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function AccountsRegistry() {
  const { can } = useAuth();
  const accounts = useQuery(accountQueries.list());

  return (
    <div className="space-y-6">
      <PageHeader
        title="Accounts"
        description="Social accounts this organization distributes to. Manual registrations are labels only: nothing is connected and no credentials are stored until an official OAuth flow succeeds in M3B."
        actions={
          can("manage_accounts") ? (
            <RegisterAccountDialog
              trigger={
                <Button>
                  <Plus /> Register Page
                </Button>
              }
            />
          ) : null
        }
      />
      <Alert>
        <ShieldAlert />
        <AlertTitle>No account is connected</AlertTitle>
        <AlertDescription>
          Every record below is unverified and disconnected. Publishing is assisted: you publish in
          the platform UI yourself and record the result here. API publishing requires Meta Page
          authorization, which is out of scope until M3B.
        </AlertDescription>
      </Alert>
      {accounts.isPending ? (
        <div className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-40 rounded-xl" />
        </div>
      ) : accounts.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load accounts</AlertTitle>
          <AlertDescription>{accounts.error.message}</AlertDescription>
        </Alert>
      ) : accounts.data.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-16 text-center">
          <h2 className="text-lg font-semibold">No accounts registered</h2>
          <p className="max-w-md text-sm text-muted-foreground">
            Register the Facebook Pages you publish to. Registration only records a name and handle
            so publications can target it; it does not connect anything.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {accounts.data.map((account) => (
            <AccountCard key={account.id} account={account} />
          ))}
        </div>
      )}
    </div>
  );
}
