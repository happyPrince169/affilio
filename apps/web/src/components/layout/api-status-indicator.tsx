"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { API_BASE_URL } from "@/lib/api/client";
import { systemQueries } from "@/lib/api/system";

export function ApiStatusIndicator() {
  const { data, isPending, isError } = useQuery(systemQueries.health());

  const state = isPending ? "checking" : isError ? "offline" : "online";
  const label =
    state === "checking" ? "Checking API…" : state === "online" ? "API online" : "API offline";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-medium text-muted-foreground"
        >
          <span
            aria-hidden
            className={cn(
              "size-2 rounded-full",
              state === "checking" && "animate-pulse bg-muted-foreground/60",
              state === "online" && "bg-success",
              state === "offline" && "bg-destructive",
            )}
          />
          <span className="hidden sm:inline">{label}</span>
        </div>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-64">
        <p className="font-mono text-[11px]">{API_BASE_URL}</p>
        {data ? (
          <p className="mt-1 opacity-80">
            {data.service} v{data.version} · {data.environment}
          </p>
        ) : null}
        {state === "offline" ? (
          <p className="mt-1 opacity-80">Start the API with `pnpm dev:api`.</p>
        ) : null}
      </TooltipContent>
    </Tooltip>
  );
}
