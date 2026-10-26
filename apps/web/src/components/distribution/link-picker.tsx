"use client";

import type { AffiliateLink } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { linkQueries } from "@/lib/api/content";

interface LinkPickerProps {
  value: AffiliateLink[];
  onChange: (links: AffiliateLink[]) => void;
  max?: number;
}

/** Ordered multi-select from the affiliate link library. Order is preserved in the snapshot. */
export function LinkPicker({ value, onChange, max = 10 }: LinkPickerProps) {
  const [q, setQ] = useState("");
  const links = useQuery(linkQueries.list({ q: q || undefined, status: "active" }));
  const chosen = new Set(value.map((l) => l.id));
  const move = (index: number, delta: number) => {
    const next = [...value];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    const [item] = next.splice(index, 1);
    next.splice(target, 0, item!);
    onChange(next);
  };
  return (
    <div className="space-y-2">
      {value.length > 0 ? (
        <ol className="space-y-1">
          {value.map((link, index) => (
            <li
              key={link.id}
              className="flex items-center gap-2 rounded-md border px-2 py-1 text-xs"
            >
              <span className="w-4 font-mono text-muted-foreground">{index + 1}</span>
              <span className="min-w-0 flex-1 truncate">
                <span className="font-medium">{link.label}</span>{" "}
                <span className="text-muted-foreground">{link.url}</span>
              </span>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => move(index, -1)}
                aria-label="Move up"
              >
                <ArrowUp />
              </Button>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => move(index, 1)}
                aria-label="Move down"
              >
                <ArrowDown />
              </Button>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => onChange(value.filter((l) => l.id !== link.id))}
                aria-label="Remove link"
              >
                <X />
              </Button>
            </li>
          ))}
        </ol>
      ) : null}
      <Input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search affiliate links…"
        aria-label="Search affiliate links"
      />
      <div className="max-h-36 overflow-auto rounded-md border">
        {links.isPending ? (
          <p className="p-2 text-xs text-muted-foreground">Loading…</p>
        ) : links.isError ? (
          <p className="p-2 text-xs text-destructive">{links.error.message}</p>
        ) : links.data.length === 0 ? (
          <p className="p-2 text-xs text-muted-foreground">
            No active links. Add some under Links.
          </p>
        ) : (
          <ul className="divide-y">
            {links.data.map((link) => (
              <li key={link.id}>
                <button
                  type="button"
                  disabled={chosen.has(link.id) || value.length >= max}
                  onClick={() => onChange([...value, link])}
                  className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-accent disabled:opacity-50"
                >
                  <Badge variant="outline" className="text-[10px]">
                    {link.platform}
                  </Badge>
                  <span className="min-w-0 flex-1 truncate">
                    <span className="font-medium">{link.label}</span>{" "}
                    <span className="text-muted-foreground">{link.url}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
