"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { Activity, Database, RefreshCw, Server, WifiOff } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { API_BASE_URL, ApiUnreachableError } from "@/lib/api/client";
import { systemQueries } from "@/lib/api/system";

function Row({
  icon: Icon,
  label,
  children,
}: {
  icon: typeof Server;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5 text-sm">
      <span className="flex items-center gap-2 text-muted-foreground">
        <Icon className="size-4" />
        {label}
      </span>
      <span className="text-right font-medium">{children}</span>
    </div>
  );
}

export function SystemStatusCard() {
  const query = useQuery(systemQueries.info());

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-2">
              <Activity className="size-4 text-brand" />
              System status
            </CardTitle>
            <CardDescription>
              Live response from <span className="font-mono text-xs">{API_BASE_URL}/api/v1</span>
            </CardDescription>
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Refresh system status"
            onClick={() => void query.refetch()}
            disabled={query.isFetching}
          >
            <RefreshCw className={cn(query.isFetching && "animate-spin")} />
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <div className="space-y-3" aria-busy>
            <Skeleton className="h-5 w-3/4" />
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="h-5 w-3/5" />
          </div>
        ) : query.isError ? (
          <Alert variant="destructive">
            <WifiOff />
            <AlertTitle>
              {query.error instanceof ApiUnreachableError ? "API unreachable" : "API error"}
            </AlertTitle>
            <AlertDescription>
              <p>{query.error.message}</p>
              <p className="mt-1">
                Start the backend with <code className="font-mono">pnpm dev:api</code> and make sure{" "}
                <code className="font-mono">NEXT_PUBLIC_API_URL</code> points at it.
              </p>
            </AlertDescription>
          </Alert>
        ) : (
          <div className="divide-y">
            <Row icon={Server} label="Service">
              {query.data.service}{" "}
              <span className="font-mono text-xs text-muted-foreground">v{query.data.version}</span>
            </Row>
            <Row icon={Activity} label="Environment">
              <Badge variant="secondary" className="font-mono text-[11px]">
                {query.data.environment}
              </Badge>
            </Row>
            <Row icon={Database} label="Database">
              <span className="inline-flex items-center gap-2">
                <span
                  aria-hidden
                  className={cn(
                    "size-2 rounded-full",
                    query.data.database.status === "ok" ? "bg-success" : "bg-destructive",
                  )}
                />
                {query.data.database.status === "ok"
                  ? `${query.data.database.dialect} · ${query.data.database.latency_ms ?? "–"} ms`
                  : "unavailable"}
              </span>
            </Row>
            <Row icon={Server} label="Python">
              <span className="font-mono text-xs">{query.data.python_version}</span>
            </Row>
            <div className="pt-3">
              <p className="mb-2 text-xs text-muted-foreground">Registered domain modules</p>
              <div className="flex flex-wrap gap-1.5">
                {query.data.modules.map((module) => (
                  <Badge key={module} variant="outline" className="font-mono text-[11px]">
                    {module}
                  </Badge>
                ))}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
