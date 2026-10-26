"use client";

import type {
  EditingStatus,
  MediaAssetSummary,
  ProductionJobDetail as JobDetailType,
  ProductionMediaRole,
  ProductionStatus,
  ProductSummary,
} from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ChevronDown,
  Film,
  LoaderCircle,
  Package,
  Pencil,
  Plus,
  Trash2,
  TriangleAlert,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/page-header";
import { MediaThumbnail } from "@/components/media/media-thumbnail";
import { JobFormDialog } from "@/components/production/job-form-dialog";
import {
  EDITING_LABEL,
  EditingStatusBadge,
  PRODUCTION_LABEL,
  ProductionStatusBadge,
} from "@/components/production/production-badges";
import { ProductSearchSelect } from "@/components/products/product-search-select";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { mediaQueries } from "@/lib/api/media";
import {
  addJobMedia,
  addJobProduct,
  changeJobStatus,
  productionQueries,
  removeJobMedia,
  removeJobProduct,
} from "@/lib/api/production";
import { formatDateTime, formatDuration, formatResolution } from "@/lib/format";

function AddProductDialog({ job, trigger }: { job: JobDetailType; trigger: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [product, setProduct] = useState<ProductSummary | null>(null);
  const [qty, setQty] = useState("1");
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () =>
      addJobProduct(job.id, { product_id: product!.id, quantity_used: Number(qty) || 1 }),
    onSuccess: () => {
      toast.success("Product attached");
      void queryClient.invalidateQueries({ queryKey: productionQueries.all() });
      setOpen(false);
      setProduct(null);
    },
    onError: (error) => toast.error("Could not attach product", { description: error.message }),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Attach a product</DialogTitle>
          <DialogDescription>
            Which product is used on set. This does not decide what the video will promote.
          </DialogDescription>
        </DialogHeader>
        <ProductSearchSelect
          value={product}
          onChange={setProduct}
          exclude={new Set(job.products.map((p) => p.product.id))}
        />
        <div className="space-y-1.5">
          <Label htmlFor="qty-used">Units used</Label>
          <Input
            id="qty-used"
            type="number"
            min={1}
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            className="w-24"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={!product || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Attach
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AddMediaDialog({ job, trigger }: { job: JobDetailType; trigger: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<"footage" | "final">("footage");
  const [selected, setSelected] = useState<MediaAssetSummary | null>(null);
  const [role, setRole] = useState<ProductionMediaRole>("footage");
  const queryClient = useQueryClient();
  const assets = useQuery({
    ...mediaQueries.assets({ q: q || undefined, asset_kind: kind, page_size: 12 }),
    enabled: open,
  });
  const linked = new Set(job.media.map((m) => m.asset.id));
  const mutation = useMutation({
    mutationFn: () => addJobMedia(job.id, { media_asset_id: selected!.id, role }),
    onSuccess: () => {
      toast.success("Video attached");
      void queryClient.invalidateQueries({ queryKey: productionQueries.all() });
      setOpen(false);
      setSelected(null);
    },
    onError: (error) => toast.error("Could not attach video", { description: error.message }),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Attach a video</DialogTitle>
          <DialogDescription>
            Link indexed footage or a finished video to this job. Optional; nothing on the NAS
            changes.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap gap-2">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search videos…"
            className="flex-1"
            aria-label="Search videos"
          />
          <Select
            value={kind}
            onValueChange={(v) => {
              setKind(v as "footage" | "final");
              setRole(v === "final" ? "final" : "footage");
            }}
          >
            <SelectTrigger className="w-32" aria-label="Library">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="footage">Footage</SelectItem>
              <SelectItem value="final">Finals</SelectItem>
            </SelectContent>
          </Select>
          <Select value={role} onValueChange={(v) => setRole(v as ProductionMediaRole)}>
            <SelectTrigger className="w-32" aria-label="Role">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="footage">Footage</SelectItem>
              <SelectItem value="final">Final</SelectItem>
              <SelectItem value="reference">Reference</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {assets.isPending ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Loading…</p>
        ) : assets.isError ? (
          <p className="py-6 text-center text-sm text-destructive">{assets.error.message}</p>
        ) : assets.data.items.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No videos found.</p>
        ) : (
          <div className="grid max-h-72 grid-cols-3 gap-2 overflow-y-auto sm:grid-cols-4">
            {assets.data.items.map((asset) => (
              <button
                key={asset.id}
                type="button"
                disabled={linked.has(asset.id)}
                onClick={() => setSelected(asset)}
                aria-pressed={selected?.id === asset.id}
                className={`overflow-hidden rounded-lg border text-left outline-none focus-visible:ring-2 disabled:opacity-40 ${selected?.id === asset.id ? "border-brand ring-2 ring-brand/40" : "hover:border-brand/50"}`}
              >
                <MediaThumbnail asset={asset} className="aspect-[4/5] w-full" />
                <div className="space-y-0.5 p-2">
                  <p className="truncate text-xs font-medium">{asset.title}</p>
                  <p className="font-mono text-[10px] text-muted-foreground">
                    {formatResolution(asset.width, asset.height)} ·{" "}
                    {formatDuration(asset.duration_seconds)}
                  </p>
                </div>
              </button>
            ))}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={!selected || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Attach
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Detail({ job }: { job: JobDetailType }) {
  const queryClient = useQueryClient();
  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: productionQueries.all() });
  const status = useMutation({
    mutationFn: (body: { status?: ProductionStatus; editing_status?: EditingStatus }) =>
      changeJobStatus(job.id, body),
    onSuccess: () => {
      toast.success("Status updated");
      invalidate();
    },
    onError: (error) => toast.error("Status not changed", { description: error.message }),
  });
  const detachProduct = useMutation({
    mutationFn: (productId: string) => removeJobProduct(job.id, productId),
    onSuccess: invalidate,
    onError: (error) => toast.error("Could not detach", { description: error.message }),
  });
  const detachMedia = useMutation({
    mutationFn: (assetId: string) => removeJobMedia(job.id, assetId),
    onSuccess: invalidate,
    onError: (error) => toast.error("Could not detach", { description: error.message }),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title={job.title}
        description={`${job.job_number}${job.planned_filming_date ? ` · filming ${job.planned_filming_date}` : ""}${job.deadline ? ` · due ${job.deadline}` : ""}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <ProductionStatusBadge status={job.status} />
            <EditingStatusBadge status={job.editing_status} />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" disabled={status.isPending}>
                  Change status <ChevronDown />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>Production</DropdownMenuLabel>
                {job.allowed_transitions.map((s) => (
                  <DropdownMenuItem key={s} onClick={() => status.mutate({ status: s })}>
                    {PRODUCTION_LABEL[s]}
                  </DropdownMenuItem>
                ))}
                <DropdownMenuSeparator />
                <DropdownMenuLabel>
                  Editing{job.status !== "filmed" ? " (after filming)" : ""}
                </DropdownMenuLabel>
                {job.allowed_editing_transitions.map((s) => (
                  <DropdownMenuItem
                    key={s}
                    disabled={job.status !== "filmed" && s !== "not_started"}
                    onClick={() => status.mutate({ editing_status: s })}
                  >
                    {EDITING_LABEL[s]}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
            <JobFormDialog
              key={job.updated_at}
              job={job}
              trigger={
                <Button variant="outline" size="sm">
                  <Pencil /> Edit
                </Button>
              }
            />
            <Button asChild variant="ghost" size="sm">
              <Link href="/production">
                <ArrowLeft /> Board
              </Link>
            </Button>
          </div>
        }
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Brief</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {job.description ? (
              <p className="whitespace-pre-wrap">{job.description}</p>
            ) : (
              <p className="text-muted-foreground">No description.</p>
            )}
            {job.staff_labels.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {job.staff_labels.map((label) => (
                  <Badge key={label} variant="secondary" className="gap-1">
                    <Users className="size-3" /> {label}
                  </Badge>
                ))}
              </div>
            ) : null}
            {job.notes ? (
              <p className="rounded-md bg-muted p-3 text-xs whitespace-pre-wrap">{job.notes}</p>
            ) : null}
            <p className="text-xs text-muted-foreground">
              Created {formatDateTime(job.created_at)} · status changed{" "}
              {formatDateTime(job.status_changed_at)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-start justify-between gap-2">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Package className="size-4 text-brand" /> Products on set
                </CardTitle>
                <CardDescription>Optional. What was used while filming.</CardDescription>
              </div>
              <AddProductDialog
                job={job}
                trigger={
                  <Button size="icon-sm" variant="outline" aria-label="Attach product">
                    <Plus />
                  </Button>
                }
              />
            </div>
          </CardHeader>
          <CardContent>
            {job.products.length === 0 ? (
              <p className="text-sm text-muted-foreground">No products attached.</p>
            ) : (
              <ul className="divide-y">
                {job.products.map((p) => (
                  <li key={p.id} className="flex items-center justify-between gap-2 py-2 text-sm">
                    <div className="min-w-0">
                      <Link
                        href={`/products/${p.product.id}`}
                        className="block truncate font-medium hover:text-brand hover:underline"
                      >
                        {p.product.name}
                      </Link>
                      <span className="text-xs text-muted-foreground">
                        × {p.quantity_used}
                        {p.order_number ? ` · from ${p.order_number}` : ""}
                      </span>
                    </div>
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      aria-label={`Detach ${p.product.name}`}
                      onClick={() => detachProduct.mutate(p.product.id)}
                    >
                      <Trash2 />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-2">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Film className="size-4 text-brand" /> Videos
              </CardTitle>
              <CardDescription>
                Optional links to indexed footage and finished videos. Files stay where editors put
                them.
              </CardDescription>
            </div>
            <AddMediaDialog
              job={job}
              trigger={
                <Button size="sm" variant="outline">
                  <Plus /> Attach video
                </Button>
              }
            />
          </div>
        </CardHeader>
        <CardContent>
          {job.media.length === 0 ? (
            <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
              No videos attached yet.
            </p>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {job.media.map((m) => (
                <div key={m.id} className="flex gap-3 rounded-lg border p-2">
                  <Link href={`/content/${m.asset.id}`} className="shrink-0">
                    <MediaThumbnail asset={m.asset} className="aspect-[4/5] w-16 rounded-md" />
                  </Link>
                  <div className="min-w-0 flex-1 space-y-1">
                    <Link
                      href={`/content/${m.asset.id}`}
                      className="block truncate text-sm font-medium hover:text-brand hover:underline"
                    >
                      {m.asset.title}
                    </Link>
                    <div className="flex flex-wrap gap-1">
                      <Badge variant="outline" className="capitalize">
                        {m.role}
                      </Badge>
                      {m.asset.asset_kind === "final" && m.asset.handoff_status ? (
                        <Badge variant="outline" className="capitalize">
                          {m.asset.handoff_status.replace("_", " ")}
                        </Badge>
                      ) : null}
                    </div>
                    <Button
                      size="xs"
                      variant="ghost"
                      className="text-destructive"
                      onClick={() => detachMedia.mutate(m.asset.id)}
                    >
                      <Trash2 /> Detach
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export function JobDetailRoute() {
  const params = useParams<{ id: string }>();
  const query = useQuery(productionQueries.job(params.id));
  if (query.isPending) return <Skeleton className="h-96 rounded-xl" aria-busy />;
  if (query.isError) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <div className="space-y-4">
        <Alert variant={notFound ? "default" : "destructive"}>
          <TriangleAlert />
          <AlertTitle>{notFound ? "Job not found" : "Could not load job"}</AlertTitle>
          <AlertDescription>{query.error.message}</AlertDescription>
        </Alert>
        <Button asChild variant="outline">
          <Link href="/production">
            <ArrowLeft /> Back to board
          </Link>
        </Button>
      </div>
    );
  }
  return <Detail job={query.data} />;
}
