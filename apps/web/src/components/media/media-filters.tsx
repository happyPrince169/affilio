"use client";

import type { MediaAssetFilters, MediaSortKey } from "@affilio/shared-types";
import { Search, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const ALL = "all";

const SORT_LABELS: Record<MediaSortKey, string> = {
  newest: "Newest first",
  oldest: "Oldest first",
  title: "Title A–Z",
  duration: "Longest first",
  size: "Largest first",
  last_seen: "Recently seen",
};

interface MediaFiltersProps {
  filters: MediaAssetFilters;
  onChange: (next: MediaAssetFilters) => void;
  disabled?: boolean;
}

export function MediaFilters({ filters, onChange, disabled }: MediaFiltersProps) {
  const [search, setSearch] = useState(filters.q ?? "");
  // Adopt an externally changed query (e.g. "Clear" or browser navigation) during render.
  const [syncedQ, setSyncedQ] = useState(filters.q ?? "");
  if (syncedQ !== (filters.q ?? "")) {
    setSyncedQ(filters.q ?? "");
    setSearch(filters.q ?? "");
  }

  // Debounce free-text search so each keystroke does not hit the API.
  useEffect(() => {
    const handle = setTimeout(() => {
      if ((filters.q ?? "") !== search) onChange({ ...filters, q: search || undefined, page: 1 });
    }, 300);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const update = (patch: Partial<MediaAssetFilters>) => onChange({ ...filters, ...patch, page: 1 });
  const hasActiveFilters =
    Boolean(filters.q) ||
    Boolean(filters.status) ||
    Boolean(filters.orientation) ||
    filters.has_proxy !== undefined ||
    Boolean(filters.duplicates_only);

  return (
    <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
      <div className="relative flex-1">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search by title or path…"
          className="pl-8"
          disabled={disabled}
          aria-label="Search media"
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={filters.status ?? ALL}
          onValueChange={(value) =>
            update({ status: value === ALL ? undefined : (value as MediaAssetFilters["status"]) })
          }
          disabled={disabled}
        >
          <SelectTrigger className="w-36" aria-label="Status filter">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All statuses</SelectItem>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="missing">Missing</SelectItem>
            <SelectItem value="unsupported">Unsupported</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={filters.orientation ?? ALL}
          onValueChange={(value) =>
            update({
              orientation: value === ALL ? undefined : (value as MediaAssetFilters["orientation"]),
            })
          }
          disabled={disabled}
        >
          <SelectTrigger className="w-36" aria-label="Orientation filter">
            <SelectValue placeholder="Orientation" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Any orientation</SelectItem>
            <SelectItem value="vertical">Vertical</SelectItem>
            <SelectItem value="horizontal">Horizontal</SelectItem>
            <SelectItem value="square">Square</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={filters.has_proxy === undefined ? ALL : filters.has_proxy ? "yes" : "no"}
          onValueChange={(value) =>
            update({ has_proxy: value === ALL ? undefined : value === "yes" })
          }
          disabled={disabled}
        >
          <SelectTrigger className="w-32" aria-label="Proxy filter">
            <SelectValue placeholder="Proxy" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Proxy: any</SelectItem>
            <SelectItem value="yes">Has proxy</SelectItem>
            <SelectItem value="no">No proxy</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={filters.sort ?? "newest"}
          onValueChange={(value) => update({ sort: value as MediaSortKey })}
          disabled={disabled}
        >
          <SelectTrigger className="w-40" aria-label="Sort order">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(SORT_LABELS) as MediaSortKey[]).map((key) => (
              <SelectItem key={key} value={key}>
                {SORT_LABELS[key]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant={filters.duplicates_only ? "secondary" : "outline"}
          size="sm"
          onClick={() => update({ duplicates_only: !filters.duplicates_only || undefined })}
          disabled={disabled}
          aria-pressed={Boolean(filters.duplicates_only)}
        >
          Duplicates
        </Button>
        {hasActiveFilters ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onChange({ sort: filters.sort, page: 1, page_size: filters.page_size })}
          >
            <X /> Clear
          </Button>
        ) : null}
      </div>
    </div>
  );
}
