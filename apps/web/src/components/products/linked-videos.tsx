"use client";

import type { ProductMediaLink } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Star, Trash2 } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { MediaThumbnail } from "@/components/media/media-thumbnail";
import { LinkVideoDialog } from "@/components/products/link-video-dialog";
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
import { Skeleton } from "@/components/ui/skeleton";
import { productQueries, unlinkProductMedia, updateProductMediaLink } from "@/lib/api/products";
import { formatDuration, formatRelative, formatResolution } from "@/lib/format";

function LinkedVideo({ link, productId }: { link: ProductMediaLink; productId: string }) {
  const queryClient = useQueryClient();
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: productQueries.all() });
  const primary = useMutation({
    mutationFn: () =>
      updateProductMediaLink(productId, link.media_asset_id, { is_primary: !link.is_primary }),
    onSuccess: invalidate,
    onError: (error) => toast.error("Could not update link", { description: error.message }),
  });
  const unlink = useMutation({
    mutationFn: () => unlinkProductMedia(productId, link.media_asset_id),
    onSuccess: () => {
      toast.success("Video unlinked");
      invalidate();
    },
    onError: (error) => toast.error("Could not unlink", { description: error.message }),
  });

  return (
    <div className="flex gap-3 rounded-lg border p-2">
      <Link href={`/content/${link.media_asset_id}`} className="shrink-0">
        <MediaThumbnail asset={link.asset} className="aspect-[4/5] w-20 rounded-md" />
      </Link>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-start justify-between gap-2">
          <Link
            href={`/content/${link.media_asset_id}`}
            className="truncate text-sm font-medium hover:text-brand hover:underline"
          >
            {link.asset.title}
          </Link>
          {link.is_primary ? (
            <Badge variant="outline" className="shrink-0 gap-1 border-brand/50 text-brand">
              <Star className="size-3" /> Primary
            </Badge>
          ) : null}
        </div>
        <p className="font-mono text-[11px] text-muted-foreground">
          {formatResolution(link.asset.width, link.asset.height)} ·{" "}
          {formatDuration(link.asset.duration_seconds)} · linked {formatRelative(link.created_at)}
        </p>
        {link.notes ? <p className="text-xs">{link.notes}</p> : null}
        <div className="flex gap-1 pt-1">
          <Button
            variant="ghost"
            size="xs"
            onClick={() => primary.mutate()}
            disabled={primary.isPending}
          >
            <Star className={link.is_primary ? "fill-brand text-brand" : ""} />
            {link.is_primary ? "Unset primary" : "Make primary"}
          </Button>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="ghost" size="xs" className="text-destructive">
                <Trash2 /> Unlink
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Unlink this video?</AlertDialogTitle>
                <AlertDialogDescription>
                  Only the mapping is removed. The video stays in the library and on the NAS.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={() => unlink.mutate()}>Unlink</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>
    </div>
  );
}

export function LinkedVideos({ productId }: { productId: string }) {
  const links = useQuery(productQueries.media(productId));
  const linkedIds = new Set((links.data ?? []).map((l) => l.media_asset_id));

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>Linked videos</CardTitle>
            <CardDescription>
              Media assets that feature this product. Mappings only; files are never moved.
            </CardDescription>
          </div>
          <LinkVideoDialog
            productId={productId}
            linkedAssetIds={linkedIds}
            trigger={
              <Button size="sm" variant="outline">
                <Link2 /> Link video
              </Button>
            }
          />
        </div>
      </CardHeader>
      <CardContent>
        {links.isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : links.isError ? (
          <p className="text-sm text-destructive">{links.error.message}</p>
        ) : links.data.length === 0 ? (
          <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            No videos linked yet.
          </p>
        ) : (
          <div className="grid gap-2 lg:grid-cols-2">
            {links.data.map((link) => (
              <LinkedVideo key={link.id} link={link} productId={productId} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
