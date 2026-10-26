"use client";

import { useQuery } from "@tanstack/react-query";
import { Users } from "lucide-react";
import Link from "next/link";

import { Card, CardContent } from "@/components/ui/card";
import { accountQueries } from "@/lib/api/accounts";

export function AccountsStatsTile() {
  const accounts = useQuery(accountQueries.list({ include_disabled: false }));
  const total = accounts.data?.length ?? null;
  const connected =
    accounts.data?.filter((a) => a.is_verified && a.status === "connected").length ?? 0;
  const note = accounts.data
    ? total === 0
      ? "No accounts registered. Register Pages under Accounts."
      : `${connected} connected · ${total! - connected} manual (unverified). API publishing arrives in M3B.`
    : accounts.isError
      ? "Account stats unavailable."
      : "Loading accounts…";
  return (
    <Link href="/accounts" className="rounded-xl outline-none focus-visible:ring-2">
      <Card className="h-full gap-3 py-5 transition-colors hover:border-brand/50">
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-muted-foreground">Registered accounts</span>
            <Users className="size-4 text-muted-foreground" />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-semibold tracking-tight">
              {total === null ? "—" : total}
            </span>
            <span className="rounded-md border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
              M3A
            </span>
          </div>
          <p className="text-xs text-muted-foreground">{note}</p>
        </CardContent>
      </Card>
    </Link>
  );
}
