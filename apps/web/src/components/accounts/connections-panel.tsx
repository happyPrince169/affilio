"use client";

import type { PlatformConnection } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Link2, RefreshCw, ShieldOff } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  accountQueries,
  revokeConnection,
  runMockAction,
  verifyConnection,
} from "@/lib/api/accounts";
import { formatDateTime } from "@/lib/format";

function ConnectionStatusBadge({ connection }: { connection: PlatformConnection }) {
  if (connection.status === "active")
    return <Badge className="text-success-foreground bg-success">Active</Badge>;
  return (
    <Badge variant="destructive" className="capitalize">
      {connection.status}
    </Badge>
  );
}

function ConnectionRow({ connection, mock }: { connection: PlatformConnection; mock: boolean }) {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: accountQueries.all() });
  const verify = useMutation({
    mutationFn: () => verifyConnection(connection.id),
    onSuccess: (saved) => {
      toast[saved.status === "active" ? "success" : "warning"](
        saved.status === "active" ? "Grant verified" : `Grant is ${saved.status}`,
      );
      void invalidate();
    },
    onError: (error) => toast.error("Verification failed", { description: error.message }),
  });
  const revoke = useMutation({
    mutationFn: () => revokeConnection(connection.id),
    onSuccess: () => {
      toast.success("Connection revoked; every Page of this grant is disconnected");
      void invalidate();
    },
    onError: (error) => toast.error("Could not revoke", { description: error.message }),
  });
  const mockRevokeUser = useMutation({
    mutationFn: () => runMockAction({ action: "revoke_user_token", connection_id: connection.id }),
    onSuccess: () => toast.info("Mock: user token revoked at the provider. Verify to detect it."),
    onError: (error) => toast.error("Mock action failed", { description: error.message }),
  });

  return (
    <li className="flex flex-col gap-3 px-3 py-3 text-sm sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium">
            {connection.external_user_name ?? `Meta user ${connection.external_user_id}`}
          </p>
          <ConnectionStatusBadge connection={connection} />
          {connection.is_mock ? (
            <Badge className="gap-1 bg-amber-500 text-black hover:bg-amber-500">
              <FlaskConical className="size-3" /> MOCK
            </Badge>
          ) : null}
        </div>
        <p className="text-xs text-muted-foreground">
          {connection.page_count} Page{connection.page_count === 1 ? "" : "s"} discovered ·{" "}
          {connection.linked_account_count} connected · granted by {connection.connected_by_label}{" "}
          {formatDateTime(connection.connected_at)}
        </p>
        <p className="text-xs text-muted-foreground">
          Scopes: {connection.granted_scopes.join(", ") || "none"}
          {connection.missing_scopes.length ? (
            <span className="text-destructive">
              {" "}
              · missing: {connection.missing_scopes.join(", ")}
            </span>
          ) : null}
        </p>
        <p className="text-xs text-muted-foreground">
          User token expires {formatDateTime(connection.token_expires_at)} · last checked{" "}
          {formatDateTime(connection.last_checked_at)}
          {connection.last_error ? (
            <span className="text-destructive"> · {connection.last_error}</span>
          ) : null}
        </p>
      </div>
      {can("manage_accounts") ? (
        <div className="flex flex-wrap items-center gap-1">
          {connection.status === "active" ? (
            <Button asChild variant="outline" size="sm">
              <Link href={`/accounts/connect/facebook/${connection.id}`}>
                <Link2 /> Choose Pages
              </Link>
            </Button>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={() => verify.mutate()}
            disabled={verify.isPending || connection.status !== "active"}
          >
            <RefreshCw className={verify.isPending ? "animate-spin" : ""} /> Verify
          </Button>
          {mock && connection.status === "active" ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => mockRevokeUser.mutate()}
              disabled={mockRevokeUser.isPending}
              title="Mock only: simulate the person removing the app on Facebook"
            >
              <FlaskConical /> Simulate revoke
            </Button>
          ) : null}
          {connection.status === "active" ? (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" size="sm">
                  <ShieldOff /> Revoke
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Revoke this Meta authorization?</AlertDialogTitle>
                  <AlertDialogDescription>
                    AFFILIO revokes the app grant at the provider when it can and makes every stored
                    token of this connection unusable. All {connection.linked_account_count}{" "}
                    connected Page(s) become disconnected; routing rules and publication history are
                    kept. Reconnect later to restore them.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Keep</AlertDialogCancel>
                  <AlertDialogAction onClick={() => revoke.mutate()}>Revoke</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export function ConnectionsPanel() {
  const connections = useQuery(accountQueries.connections());
  const config = useQuery(accountQueries.connectionConfig());
  const mock = config.data?.mode === "mock";
  const queryClient = useQueryClient();
  const outageToggle = useMutation({
    mutationFn: (on: boolean) => runMockAction({ action: on ? "outage_on" : "outage_off" }),
    onSuccess: (result) => {
      toast.info(result.outage ? "Mock outage ON: provider calls fail" : "Mock outage OFF");
      void queryClient.invalidateQueries({ queryKey: accountQueries.all() });
    },
    onError: (error) => toast.error("Mock action failed", { description: error.message }),
  });

  if (connections.isPending || !connections.data) return null;
  if (connections.data.length === 0 && !mock) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          Meta authorizations
          {mock ? (
            <Badge className="gap-1 bg-amber-500 text-black hover:bg-amber-500">
              <FlaskConical className="size-3" /> MOCK
            </Badge>
          ) : null}
        </CardTitle>
        <CardDescription>
          One entry per person who authorized AFFILIO. Pages are connected from a grant; the tokens
          themselves are encrypted on the server and never shown.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {connections.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No authorization yet.</p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {connections.data.map((c) => (
              <ConnectionRow key={c.id} connection={c} mock={mock} />
            ))}
          </ul>
        )}
        {mock ? (
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <FlaskConical className="size-3" /> Mock switches:
            <Button
              variant="outline"
              size="sm"
              onClick={() => outageToggle.mutate(true)}
              disabled={outageToggle.isPending}
            >
              Outage on
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => outageToggle.mutate(false)}
              disabled={outageToggle.isPending}
            >
              Outage off
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
