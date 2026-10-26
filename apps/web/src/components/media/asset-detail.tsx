"use client";

import type { MediaAssetDetail, MediaVariant } from "@affilio/shared-types";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Copy, TriangleAlert } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { AssetStatusBadge, ThumbnailStatusBadge } from "@/components/media/media-badges";
import { HandoffBadge } from "@/components/media/handoff-badge";
import { HandoffReviewCard } from "@/components/media/handoff-review";
import { MediaThumbnail } from "@/components/media/media-thumbnail";
import { LinkedProductsCard } from "@/components/products/linked-products-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api/client";
import { mediaQueries } from "@/lib/api/media";
import {
  codecLabel,
  formatBitrate,
  formatBytes,
  formatDateTime,
  formatDuration,
  formatResolution,
} from "@/lib/format";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b py-2 text-sm last:border-b-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium break-all">{value ?? "–"}</dd>
    </div>
  );
}

function VariantRow({ variant }: { variant: MediaVariant }) {
  return (
    <TableRow>
      <TableCell className="capitalize">{variant.kind}</TableCell>
      <TableCell>
        <Badge variant={variant.status === "present" ? "outline" : "destructive"}>
          {variant.status}
        </Badge>
      </TableCell>
      <TableCell className="font-mono text-xs">
        <span className="text-muted-foreground">{variant.root_key}/</span>
        {variant.relative_path}
      </TableCell>
      <TableCell className="font-mono text-xs">{formatBytes(variant.file_size)}</TableCell>
      <TableCell className="font-mono text-xs">
        {variant.video_codec ? (
          <>
            {codecLabel(variant.video_codec)}
            {variant.bit_depth ? ` ${variant.bit_depth}-bit` : ""}
            {variant.pixel_format ? ` (${variant.pixel_format})` : ""}
          </>
        ) : (
          "–"
        )}
      </TableCell>
      <TableCell className="font-mono text-xs">
        {formatResolution(variant.width, variant.height)}
      </TableCell>
      <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
        {formatDateTime(variant.modified_at)}
      </TableCell>
      <TableCell className="text-xs text-destructive">{variant.probe_error ?? ""}</TableCell>
    </TableRow>
  );
}

function Detail({ asset }: { asset: MediaAssetDetail }) {
  const proxy = asset.variants.find((v) => v.kind === "proxy" && v.status === "present");
  return (
    <div className="space-y-6">
      <PageHeader
        title={asset.title}
        description={asset.file_name ?? undefined}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" className="capitalize">
              {asset.asset_kind}
            </Badge>
            {asset.asset_kind === "final" && asset.handoff_status ? (
              <HandoffBadge status={asset.handoff_status} />
            ) : null}
            <AssetStatusBadge status={asset.status} />
            {asset.duplicate_of_id ? (
              <Button asChild variant="outline" size="sm">
                <Link href={`/content/${asset.duplicate_of_id}`}>
                  <Copy /> Duplicate of…
                </Link>
              </Button>
            ) : null}
            <Button asChild variant="ghost" size="sm">
              <Link href="/content">
                <ArrowLeft /> Library
              </Link>
            </Button>
          </div>
        }
      />

      {asset.probe_error ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>ffprobe could not read this file</AlertTitle>
          <AlertDescription>{asset.probe_error}</AlertDescription>
        </Alert>
      ) : null}
      {proxy && proxy.bit_depth && proxy.bit_depth > 8 ? (
        <Alert>
          <TriangleAlert />
          <AlertTitle>
            Proxy is {proxy.bit_depth}-bit {codecLabel(proxy.video_codec)}
          </AlertTitle>
          <AlertDescription>
            High-bit-depth HEVC proxies are used for thumbnails only. They are not treated as
            publish-ready; an H.264/AAC export pipeline is planned separately.
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <CardContent className="space-y-3">
            <MediaThumbnail
              asset={asset}
              className="mx-auto aspect-[9/16] max-h-[480px] w-full max-w-xs rounded-lg"
            />
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Thumbnail</span>
              <ThumbnailStatusBadge status={asset.thumbnail_status} />
            </div>
            {asset.thumbnail_error ? (
              <p className="text-xs text-destructive">{asset.thumbnail_error}</p>
            ) : null}
            <p className="text-xs text-muted-foreground">
              Playback is not available in this milestone; thumbnails come from the proxy when one
              is linked, otherwise from the original.
            </p>
          </CardContent>
        </Card>

        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>Technical metadata</CardTitle>
            <CardDescription>From the original file, as reported by ffprobe.</CardDescription>
          </CardHeader>
          <CardContent>
            <dl>
              <Row label="Duration" value={formatDuration(asset.duration_seconds)} />
              <Row
                label="Resolution"
                value={
                  <>
                    {formatResolution(asset.width, asset.height)}
                    {asset.orientation ? (
                      <span className="ml-2 text-muted-foreground capitalize">
                        {asset.orientation}
                      </span>
                    ) : null}
                  </>
                }
              />
              <Row label="Frame rate" value={asset.frame_rate ? `${asset.frame_rate} fps` : "–"} />
              <Row
                label="Video codec"
                value={
                  asset.video_codec
                    ? `${codecLabel(asset.video_codec)}${asset.bit_depth ? ` · ${asset.bit_depth}-bit` : ""}`
                    : "–"
                }
              />
              <Row label="Pixel format" value={asset.pixel_format} />
              <Row label="Audio codec" value={codecLabel(asset.audio_codec)} />
              <Row label="Bitrate" value={formatBitrate(asset.bit_rate)} />
              <Row label="Container" value={asset.container_format} />
              <Row label="Rotation" value={asset.rotation != null ? `${asset.rotation}°` : "–"} />
              <Row label="File size" value={formatBytes(asset.file_size)} />
              <Row
                label="Path"
                value={
                  <span className="font-mono text-xs">
                    {asset.asset_kind === "final" ? "finals" : "originals"}/{asset.relative_path}
                  </span>
                }
              />
              <Row label="First seen" value={formatDateTime(asset.first_seen_at)} />
              <Row label="Last seen" value={formatDateTime(asset.last_seen_at)} />
              {asset.missing_since ? (
                <Row label="Missing since" value={formatDateTime(asset.missing_since)} />
              ) : null}
            </dl>
          </CardContent>
        </Card>
      </div>

      {asset.asset_kind === "final" ? <HandoffReviewCard asset={asset} /> : null}

      <LinkedProductsCard assetId={asset.id} />

      <Card>
        <CardHeader>
          <CardTitle>Files</CardTitle>
          <CardDescription>
            Every file that belongs to this asset, located as logical root + relative path.
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Kind</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Location</TableHead>
                <TableHead>Size</TableHead>
                <TableHead>Codec</TableHead>
                <TableHead>Resolution</TableHead>
                <TableHead>Modified</TableHead>
                <TableHead>Probe</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {asset.variants.map((variant) => (
                <VariantRow key={variant.id} variant={variant} />
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {asset.duplicates.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Duplicates of this asset</CardTitle>
            <CardDescription>
              Files with identical content (confirmed by full SHA-256) found elsewhere.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1 font-mono text-xs">
              {asset.duplicates.map((id) => (
                <li key={id}>
                  <Link href={`/content/${id}`} className="text-brand hover:underline">
                    {id}
                  </Link>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

export function AssetDetail({ id }: { id: string }) {
  const query = useQuery(mediaQueries.asset(id));

  if (query.isPending) {
    return (
      <div className="space-y-6" aria-busy>
        <Skeleton className="h-8 w-64" />
        <div className="grid gap-6 lg:grid-cols-5">
          <Skeleton className="aspect-[9/16] max-h-[480px] rounded-xl lg:col-span-2" />
          <Skeleton className="h-96 rounded-xl lg:col-span-3" />
        </div>
      </div>
    );
  }
  if (query.isError) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <div className="space-y-4">
        <Alert variant={notFound ? "default" : "destructive"}>
          <TriangleAlert />
          <AlertTitle>{notFound ? "Asset not found" : "Could not load asset"}</AlertTitle>
          <AlertDescription>{query.error.message}</AlertDescription>
        </Alert>
        <Button asChild variant="outline">
          <Link href="/content">
            <ArrowLeft /> Back to library
          </Link>
        </Button>
      </div>
    );
  }
  return <Detail asset={query.data} />;
}
