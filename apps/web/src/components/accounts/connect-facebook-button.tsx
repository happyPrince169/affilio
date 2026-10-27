"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { FlaskConical, Link2, LoaderCircle, Settings2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { accountQueries, startFacebookConnection } from "@/lib/api/accounts";

const SCENARIO_LABELS: Record<string, string> = {
  success: "Success (3 Pages)",
  missing_permissions: "Missing pages_manage_posts",
  no_pages: "No Pages with a role",
  denied: "User denies consent",
  outage: "Meta outage",
  invalid_app: "Token for another app",
};

/**
 * "Connect Facebook Page": starts the server-side OAuth attempt and navigates the browser to
 * the authorize URL. In mock mode the URL is AFFILIO's own callback and a scenario can be chosen;
 * the mock badge makes that unmistakable.
 */
export function ConnectFacebookButton() {
  const config = useQuery(accountQueries.connectionConfig());
  const [scenario, setScenario] = useState("success");
  const start = useMutation({
    mutationFn: () =>
      startFacebookConnection(config.data?.mode === "mock" ? { mock_scenario: scenario } : {}),
    onSuccess: (started) => {
      window.location.assign(started.authorize_url);
    },
    onError: (error) =>
      toast.error("Could not start the connection", { description: error.message }),
  });

  if (config.isPending) return null;
  if (config.isError || !config.data) return null;
  const { mode, configured, missing } = config.data;

  if (!configured) {
    return (
      <div className="flex items-center gap-2">
        <Button disabled variant="outline" title="Configure the Meta app first">
          <Settings2 /> Connect Facebook Page
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {mode === "mock" ? (
        <>
          <Badge
            className="gap-1 bg-amber-500 text-black hover:bg-amber-500"
            title="META_PROVIDER_MODE=mock"
          >
            <FlaskConical className="size-3" /> MOCK
          </Badge>
          <Select value={scenario} onValueChange={setScenario}>
            <SelectTrigger className="w-56" size="sm" aria-label="Mock scenario">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {config.data.mock_scenarios.map((s) => (
                <SelectItem key={s} value={s}>
                  {SCENARIO_LABELS[s] ?? s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </>
      ) : null}
      <Button onClick={() => start.mutate()} disabled={start.isPending || missing.length > 0}>
        {start.isPending ? <LoaderCircle className="animate-spin" /> : <Link2 />}
        {mode === "mock" ? "Connect mock Facebook Page" : "Connect Facebook Page"}
      </Button>
    </div>
  );
}

/** Explains why connecting is unavailable: names of missing settings, never values. */
export function ConnectionConfigNotice() {
  const config = useQuery(accountQueries.connectionConfig());
  if (!config.data) return null;
  const { mode, configured, vault_ready, missing, scopes, graph_api_version } = config.data;
  if (configured) {
    if (mode !== "mock") return null;
    return (
      <Alert className="border-amber-500/50 bg-amber-500/10">
        <FlaskConical />
        <AlertTitle>Meta mock mode is active (development only)</AlertTitle>
        <AlertDescription>
          Connections made here use a deterministic in-process provider. Every mock Page is labelled
          MOCK, no Meta API is called, and mock credentials are refused by the live provider. Mock
          mode cannot be enabled in production. Requested scopes: {scopes.join(", ")} · Graph API{" "}
          {graph_api_version}.
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert variant="destructive">
      <Settings2 />
      <AlertTitle>Facebook connection is not configured</AlertTitle>
      <AlertDescription>
        <p>
          {vault_ready
            ? "The Meta app settings are missing on the API server."
            : "The credential vault key is missing on the API server, so no token can be stored."}{" "}
          Set the following in the server environment and restart (see
          docs/integrations/meta-app-setup.md):
        </p>
        <ul className="mt-1 list-disc pl-5 font-mono text-xs">
          {missing.map((name) => (
            <li key={name}>{name}</li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}
