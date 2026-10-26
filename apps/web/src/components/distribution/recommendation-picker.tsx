"use client";

import type { ChannelRecommendation, RecommendationsResponse } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { Ban, CircleCheck, Info, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { routingQueries } from "@/lib/api/routing";

interface RecommendationPickerProps {
  mediaAssetId: string | null;
  campaignId?: string;
  selected: Set<string>;
  onToggle: (accountId: string) => void;
}

function Row({
  item,
  checked,
  onToggle,
  disabled,
  warning,
}: {
  item: ChannelRecommendation;
  checked: boolean;
  onToggle: () => void;
  disabled?: boolean;
  warning?: boolean;
}) {
  return (
    <li>
      <label
        className={cn(
          "flex cursor-pointer gap-3 rounded-md border px-3 py-2 text-sm",
          disabled && "cursor-not-allowed opacity-70",
          checked && "border-brand/60 bg-brand/5",
        )}
      >
        <input
          type="checkbox"
          className="mt-1"
          checked={checked}
          onChange={onToggle}
          disabled={disabled}
          aria-label={item.display_name}
        />
        <span className="min-w-0 flex-1 space-y-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{item.display_name}</span>
            {item.eligible ? (
              <Badge
                variant={item.recommended ? "default" : "outline"}
                className="font-mono text-[10px]"
                title="Routing relevance score, not a performance prediction"
              >
                relevance {item.score}
              </Badge>
            ) : (
              <Badge variant="destructive" className="gap-1 text-[10px]">
                <Ban className="size-3" /> blocked
              </Badge>
            )}
            {item.already_targeted ? (
              <Badge variant="secondary" className="text-[10px]">
                already in campaign
              </Badge>
            ) : null}
            <span className="text-xs text-muted-foreground">
              {item.platform} · {item.connection_label}
            </span>
          </span>
          <span className="block text-xs text-muted-foreground">
            {item.blocking_reason ?? item.reasons.join(" · ")}
          </span>
          {warning && checked ? (
            <span className="flex items-center gap-1 text-xs text-warning">
              <TriangleAlert className="size-3" /> Low relevance: no category rule recommends this
              Page. Included on your explicit choice.
            </span>
          ) : null}
        </span>
      </label>
    </li>
  );
}

function Group({
  title,
  hint,
  items,
  selected,
  onToggle,
  disabled,
  warning,
}: {
  title: string;
  hint: string;
  items: ChannelRecommendation[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  disabled?: boolean;
  warning?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <p className="text-sm font-medium">
        {title} <span className="text-xs font-normal text-muted-foreground">· {hint}</span>
      </p>
      <ul className="space-y-1">
        {items.map((item) => (
          <Row
            key={item.social_account_id}
            item={item}
            checked={selected.has(item.social_account_id)}
            onToggle={() => onToggle(item.social_account_id)}
            disabled={disabled || item.already_targeted}
            warning={warning}
          />
        ))}
      </ul>
    </div>
  );
}

export function RecommendationPicker({
  mediaAssetId,
  campaignId,
  selected,
  onToggle,
}: RecommendationPickerProps) {
  const rec = useQuery(routingQueries.recommendations(mediaAssetId, campaignId));
  if (!mediaAssetId)
    return (
      <p className="text-sm text-muted-foreground">
        Pick an approved final to see Page recommendations.
      </p>
    );
  if (rec.isPending)
    return <p className="text-sm text-muted-foreground">Computing recommendations…</p>;
  if (rec.isError) return <p className="text-sm text-destructive">{rec.error.message}</p>;
  const data: RecommendationsResponse = rec.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Info className="size-3.5" />
        {data.primary_category ? (
          <>
            Detected category{" "}
            <Badge variant="default" className="text-[10px]">
              {data.primary_category.path}
            </Badge>
            {data.secondary_categories.map((c) => (
              <Badge key={c.id} variant="outline" className="text-[10px]">
                {c.name}
              </Badge>
            ))}
            {data.folder_path ? <span className="font-mono">({data.folder_path})</span> : null}
          </>
        ) : (
          <>No category detected for this video; map its folder or set one manually.</>
        )}
        <span>
          · rule engine {data.engine_version}, recommended ≥ {data.recommend_threshold}
        </span>
      </div>
      <Group
        title="Recommended"
        hint="category rules match"
        items={data.recommended}
        selected={selected}
        onToggle={onToggle}
      />
      <Group
        title="Low relevance"
        hint="eligible, but no rule recommends them"
        items={data.low_relevance}
        selected={selected}
        onToggle={onToggle}
        warning
      />
      <Group
        title="Excluded"
        hint="blocked by policy or disabled"
        items={data.blocked}
        selected={selected}
        onToggle={onToggle}
        disabled
      />
      {data.recommended.length + data.low_relevance.length + data.blocked.length === 0 ? (
        <p className="text-sm text-muted-foreground">No accounts registered yet.</p>
      ) : null}
      {data.recommended.some((r) => selected.has(r.social_account_id)) ? (
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <CircleCheck className="size-3" /> Drafts still need content approval before publishing.
        </p>
      ) : null}
    </div>
  );
}
