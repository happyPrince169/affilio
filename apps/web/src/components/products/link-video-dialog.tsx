"use client";

import type { MediaAssetSummary } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Search } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { MediaThumbnail } from "@/components/media/media-thumbnail";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api/client";
import { mediaQueries } from "@/lib/api/media";
import { linkProductMedia, productQueries } from "@/lib/api/products";
import { formatDuration, formatResolution } from "@/lib/format";

interface LinkVideoDialogProps {
  productId: string;
  linkedAssetIds: Set<string>;
  trigger: React.ReactNode;
}

export function LinkVideoDialog({ productId, linkedAssetIds, trigger }: LinkVideoDialogProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<MediaAssetSummary | null>(null);
  const [isPrimary, setIsPrimary] = useState(false);
  const [notes, setNotes] = useState("");
  const queryClient = useQueryClient();

  const assets = useQuery({
    ...mediaQueries.assets({
      q: search || undefined,
      status: "active",
      page_size: 12,
      sort: "newest",
    }),
    enabled: open,
  });

  const mutation = useMutation({
    mutationFn: () =>
      linkProductMedia(productId, {
        media_asset_id: selected!.id,
        is_primary: isPrimary,
        notes: notes.trim() || null,
      }),
    onSuccess: () => {
      toast.success("Video linked");
      void queryClient.invalidateQueries({ queryKey: productQueries.all() });
      setOpen(false);
      setSelected(null);
      setNotes("");
      setIsPrimary(false);
    },
    onError: (error) =>
      toast.error("Could not link video", {
        description:
          error instanceof ApiError && error.code === "duplicate_media_link"
            ? "This video is already linked to the product."
            : error.message,
      }),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Link a video</DialogTitle>
          <DialogDescription>
            Pick an indexed video from the Content Library. A video can be linked to several
            products; mark one of them as the primary product for that video.
          </DialogDescription>
        </DialogHeader>
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search videos by title or path…"
            className="pl-8"
            aria-label="Search videos"
          />
        </div>
        {assets.isPending ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Loading videos…</p>
        ) : assets.isError ? (
          <p className="py-6 text-center text-sm text-destructive">{assets.error.message}</p>
        ) : assets.data.items.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No active videos found. Scan the Content Library first.
          </p>
        ) : (
          <div className="grid max-h-72 grid-cols-3 gap-2 overflow-y-auto sm:grid-cols-4">
            {assets.data.items.map((asset) => {
              const already = linkedAssetIds.has(asset.id);
              const active = selected?.id === asset.id;
              return (
                <button
                  key={asset.id}
                  type="button"
                  disabled={already}
                  onClick={() => setSelected(asset)}
                  aria-pressed={active}
                  className={`overflow-hidden rounded-lg border text-left transition-colors outline-none focus-visible:ring-2 disabled:opacity-40 ${
                    active ? "border-brand ring-2 ring-brand/40" : "hover:border-brand/50"
                  }`}
                >
                  <MediaThumbnail asset={asset} className="aspect-[4/5] w-full" />
                  <div className="space-y-0.5 p-2">
                    <p className="truncate text-xs font-medium">{asset.title}</p>
                    <p className="font-mono text-[10px] text-muted-foreground">
                      {formatResolution(asset.width, asset.height)} ·{" "}
                      {formatDuration(asset.duration_seconds)}
                      {already ? " · linked" : ""}
                    </p>
                  </div>
                </button>
              );
            })}
          </div>
        )}
        <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <div className="space-y-1.5">
            <Label htmlFor="link-notes">Mapping notes</Label>
            <Textarea
              id="link-notes"
              rows={2}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="e.g. unboxing + demo, product shown from 0:05"
            />
          </div>
          <label className="flex items-center gap-2 self-end pb-2 text-sm">
            <Checkbox checked={isPrimary} onCheckedChange={(v) => setIsPrimary(v === true)} />
            Primary product for this video
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={!selected || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            Link {selected ? `“${selected.title}”` : "video"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
