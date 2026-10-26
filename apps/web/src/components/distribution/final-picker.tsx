"use client";

import type { MediaAssetSummary } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { CircleCheck, Search } from "lucide-react";
import { useState } from "react";

import { MediaThumbnail } from "@/components/media/media-thumbnail";
import { Input } from "@/components/ui/input";
import { mediaQueries } from "@/lib/api/media";
import { formatDuration } from "@/lib/format";

interface FinalPickerProps {
  selected: Set<string>;
  onToggle: (asset: MediaAssetSummary) => void;
  single?: boolean;
}

/** Lists approved final videos only; everything else is not eligible for distribution. */
export function FinalPicker({ selected, onToggle, single }: FinalPickerProps) {
  const [q, setQ] = useState("");
  const finals = useQuery(
    mediaQueries.assets({
      q: q || undefined,
      asset_kind: "final",
      handoff_status: "approved",
      status: "active",
      page_size: 24,
      sort: "newest",
    }),
  );
  return (
    <div className="space-y-2">
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search approved finals…"
          className="pl-8"
          aria-label="Search approved finals"
        />
      </div>
      <div className="max-h-72 overflow-auto rounded-md border p-2">
        {finals.isPending ? (
          <p className="p-2 text-sm text-muted-foreground">Loading…</p>
        ) : finals.isError ? (
          <p className="p-2 text-sm text-destructive">{finals.error.message}</p>
        ) : finals.data.items.length === 0 ? (
          <p className="p-2 text-sm text-muted-foreground">
            No approved final videos. Approve finals under Content Library → Finals first.
          </p>
        ) : (
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {finals.data.items.map((asset) => {
              const active = selected.has(asset.id);
              return (
                <li key={asset.id}>
                  <button
                    type="button"
                    onClick={() => onToggle(asset)}
                    aria-pressed={active}
                    className={cn(
                      "relative w-full overflow-hidden rounded-md border text-left outline-none focus-visible:ring-2",
                      active ? "border-brand ring-2 ring-brand/40" : "hover:border-brand/50",
                    )}
                  >
                    <MediaThumbnail asset={asset} className="aspect-video" />
                    {active ? (
                      <CircleCheck className="absolute top-1 right-1 size-5 rounded-full bg-background text-brand" />
                    ) : null}
                    <div className="space-y-0.5 p-2">
                      <p className="truncate text-xs font-medium">{asset.title}</p>
                      <p className="truncate text-[10px] text-muted-foreground">
                        {formatDuration(asset.duration_seconds)} · {asset.file_name}
                      </p>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {single ? (
        <p className="text-xs text-muted-foreground">Pick one approved final per composition.</p>
      ) : null}
    </div>
  );
}
