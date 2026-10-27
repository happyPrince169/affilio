"use client";

import type { SocialAccount } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ExternalLink,
  FlaskConical,
  Plus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  TriangleAlert,
  Unplug,
} from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { toast } from "sonner";

import {
  ChannelProfileDialog,
  ChannelProfileSummary,
} from "@/components/accounts/channel-profile-dialog";
import {
  ConnectFacebookButton,
  ConnectionConfigNotice,
} from "@/components/accounts/connect-facebook-button";
import { ConnectionsPanel } from "@/components/accounts/connections-panel";
import { RegisterAccountDialog } from "@/components/accounts/register-account-dialog";
import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  accountQueries,
  changeAccountStatus,
  disconnectAccount,
  runMockAction,
  startFacebookConnection,
  verifyAccount,
} from "@/lib/api/accounts";
import { formatDateTime } from "@/lib/format";

const CONNECT_ERRORS: Record<string, string> = {
  oauth_state_invalid:
    "The authorization could not be validated (expired, reused or tampered). Start again.",
  denied: "The authorization was cancelled at the consent screen.",
  provider_unavailable: "Meta could not be reached. Nothing was stored; try again later.",
  provider_error: "Meta rejected the authorization.",
  mock_credential_rejected: "A non-mock credential reached the mock provider; refused.",
  credential_vault_unavailable: "The credential vault is not configured on the server.",
  meta_not_configured: "The Meta app is not configured on the server.",
};

export function ConnectionBadge({ account }: { account: SocialAccount }) {
  const connected = account.is_verified && account.status === "connected";
  if (connected && account.is_mock)
    return (
      <Badge className="gap-1 bg-amber-500 text-black hover:bg-amber-500">
        <FlaskConical className="size-3" /> Mock connected
      </Badge>
    );
  if (connected)
    return (
      <Badge className="text-success-foreground gap-1 bg-success">
        <ShieldCheck className="size-3" /> Connected
      </Badge>
    );
  if (account.status === "disabled") return <Badge variant="outline">Disabled</Badge>;
  if (account.source === "oauth")
    return (
      <Badge variant="destructive" className="gap-1">
        <Unplug className="size-3" /> Disconnected · reconnect needed
      </Badge>
    );
  return (
    <Badge variant="secondary" className="gap-1">
      <ShieldAlert className="size-3" /> Unverified · not connected
    </Badge>
  );
}

const PERMISSION_LABELS: Record<SocialAccount["permission_status"], string> = {
  ok: "can publish",
  missing_page_task: "no CREATE_CONTENT on this Page",
  missing_scopes: "app permissions missing",
  unknown: "permissions unknown",
  not_connected: "not connected",
};

function AccountCard({ account, mock }: { account: SocialAccount; mock: boolean }) {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: accountQueries.all() });
  const toggle = useMutation({
    mutationFn: () =>
      changeAccountStatus(account.id, {
        status: account.status === "disabled" ? "disconnected" : "disabled",
      }),
    onSuccess: (saved) => {
      toast.success(saved.status === "disabled" ? "Account disabled" : "Account re-enabled");
      void invalidate();
    },
    onError: (error) => toast.error("Could not change status", { description: error.message }),
  });
  const verify = useMutation({
    mutationFn: () => verifyAccount(account.id),
    onSuccess: (saved) => {
      if (saved.status === "connected") toast.success("Page token verified");
      else toast.warning(`Page is disconnected: ${saved.disconnect_reason ?? "unknown"}`);
      void invalidate();
    },
    onError: (error) => toast.error("Verification failed", { description: error.message }),
  });
  const disconnect = useMutation({
    mutationFn: () => disconnectAccount(account.id, {}),
    onSuccess: () => {
      toast.success("Page disconnected; its stored token is revoked");
      void invalidate();
    },
    onError: (error) => toast.error("Could not disconnect", { description: error.message }),
  });
  const reconnect = useMutation({
    mutationFn: () => startFacebookConnection(mock ? { mock_scenario: "success" } : {}),
    onSuccess: (started) => window.location.assign(started.authorize_url),
    onError: (error) => toast.error("Could not start reconnection", { description: error.message }),
  });
  const mockRevoke = useMutation({
    mutationFn: () => runMockAction({ action: "revoke_page_token", account_id: account.id }),
    onSuccess: () => toast.info("Mock: Page token revoked at the provider. Verify to detect it."),
    onError: (error) => toast.error("Mock action failed", { description: error.message }),
  });
  const caps = account.capabilities as { api_publishing?: boolean; assisted_publishing?: boolean };
  const connected = account.is_verified && account.status === "connected";
  const oauth = account.source === "oauth";

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
            API publishing:{" "}
            {!caps.api_publishing
              ? "no"
              : account.can_publish
                ? account.is_mock
                  ? "simulated (mock)"
                  : "ready"
                : "when connected"}
          </Badge>
          {oauth ? (
            <Badge
              variant={account.permission_status === "ok" ? "outline" : "destructive"}
              className="gap-1"
            >
              permissions: {PERMISSION_LABELS[account.permission_status]}
            </Badge>
          ) : null}
        </div>
        {oauth ? (
          <p className="text-xs text-muted-foreground">
            {connected ? `Connected ${formatDateTime(account.connected_at)} · ` : ""}
            last verified {formatDateTime(account.last_verified_at)}
            {account.page_tasks ? ` · tasks: ${account.page_tasks.join(", ")}` : ""}
            {account.disconnect_reason ? (
              <span className="text-destructive">
                {" "}
                · {account.disconnect_reason.replaceAll("_", " ")}
              </span>
            ) : null}
          </p>
        ) : null}
        <ChannelProfileSummary accountId={account.id} />
        {account.notes ? (
          <p className="text-xs whitespace-pre-wrap text-muted-foreground">{account.notes}</p>
        ) : null}
        <div className="flex flex-col gap-2 text-xs text-muted-foreground sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
          <span>
            Registered {formatDateTime(account.created_at)}
            {account.registered_by ? ` by ${account.registered_by}` : ""}
          </span>
          <div className="flex flex-wrap items-center gap-1">
            {account.profile_url ? (
              <Button asChild variant="ghost" size="sm">
                <a href={account.profile_url} target="_blank" rel="noreferrer noopener">
                  <ExternalLink /> Open
                </a>
              </Button>
            ) : null}
            {can("manage_accounts") ? (
              <>
                {connected ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => verify.mutate()}
                    disabled={verify.isPending}
                  >
                    <RefreshCw className={verify.isPending ? "animate-spin" : ""} /> Verify
                  </Button>
                ) : null}
                {connected && mock ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => mockRevoke.mutate()}
                    disabled={mockRevoke.isPending}
                    title="Mock only: simulate Meta invalidating this Page token"
                  >
                    <FlaskConical /> Simulate revoke
                  </Button>
                ) : null}
                {connected ? (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="outline" size="sm">
                        <Unplug /> Disconnect
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Disconnect {account.display_name}?</AlertDialogTitle>
                        <AlertDialogDescription>
                          The stored Page token is revoked locally and the Page becomes
                          disconnected. Routing rules and publication history are kept; reconnect to
                          restore API access.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Keep</AlertDialogCancel>
                        <AlertDialogAction onClick={() => disconnect.mutate()}>
                          Disconnect
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                ) : null}
                {oauth && !connected && account.status !== "disabled" ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => reconnect.mutate()}
                    disabled={reconnect.isPending}
                  >
                    <RefreshCw /> Reconnect
                  </Button>
                ) : null}
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
  const router = useRouter();
  const searchParams = useSearchParams();
  const accounts = useQuery(accountQueries.list());
  const config = useQuery(accountQueries.connectionConfig());
  const mock = config.data?.mode === "mock";
  const connectError = searchParams.get("connect_error");

  useEffect(() => {
    if (!connectError) return;
    toast.error("Facebook connection failed", {
      description: CONNECT_ERRORS[connectError] ?? connectError,
    });
    router.replace("/accounts");
  }, [connectError, router]);

  const connectedCount =
    accounts.data?.filter((a) => a.is_verified && a.status === "connected").length ?? 0;
  const realConnected =
    accounts.data?.some((a) => a.is_verified && a.status === "connected" && !a.is_mock) ?? false;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Accounts"
        description="Social accounts this organization distributes to. Facebook Pages connect through Meta authorization; manual registrations are labels only until they are connected."
        actions={
          can("manage_accounts") ? (
            <div className="flex flex-wrap items-center gap-2">
              <ConnectFacebookButton />
              <RegisterAccountDialog
                trigger={
                  <Button variant="outline">
                    <Plus /> Register manually
                  </Button>
                }
              />
            </div>
          ) : null
        }
      />
      <ConnectionConfigNotice />
      {connectedCount === 0 ? (
        <Alert>
          <ShieldAlert />
          <AlertTitle>No account is connected</AlertTitle>
          <AlertDescription>
            Every record below is unverified and disconnected. Publishing is assisted: you publish
            in the platform UI yourself and record the result here. Connect a Facebook Page to
            prepare for API publishing (M3B.1B).
          </AlertDescription>
        </Alert>
      ) : !realConnected ? (
        <Alert className="border-amber-500/50 bg-amber-500/10">
          <FlaskConical />
          <AlertTitle>Only mock Pages are connected</AlertTitle>
          <AlertDescription>
            Mock connections exercise the connection lifecycle without Meta. They are not real
            Facebook Pages and API publishing remains unavailable.
          </AlertDescription>
        </Alert>
      ) : null}
      <ConnectionsPanel />
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
          <h2 className="text-lg font-semibold">No accounts yet</h2>
          <p className="max-w-md text-sm text-muted-foreground">
            Connect the Facebook Pages you publish to, or register a Page manually as a label so
            publications can target it before it is connected.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {accounts.data.map((account) => (
            <AccountCard key={account.id} account={account} mock={mock} />
          ))}
        </div>
      )}
    </div>
  );
}
