"use client";

import type {
  PurchaseOrderDetail as OrderDetailType,
  PurchaseOrderStatus,
} from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ChevronDown,
  ExternalLink,
  LoaderCircle,
  PackageCheck,
  Pencil,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/page-header";
import { ORDER_LABEL, OrderStatusBadge } from "@/components/procurement/procurement-badges";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api/client";
import {
  changeOrderStatus,
  procurementQueries,
  receiveOrder,
  updateOrder,
} from "@/lib/api/procurement";
import { formatDateTime, formatMoney } from "@/lib/format";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b py-2 text-sm last:border-b-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium break-all">{value ?? "—"}</dd>
    </div>
  );
}

function EditOrderDialog({ order }: { order: OrderDetailType }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    supplier_name: order.supplier_name ?? "",
    platform_order_ref: order.platform_order_ref ?? "",
    currency: order.currency ?? "",
    shipping_cost: order.shipping_cost ?? "",
    tracking_number: order.tracking_number ?? "",
    tracking_url: order.tracking_url ?? "",
    expected_delivery_at: order.expected_delivery_at ? order.expected_delivery_at.slice(0, 10) : "",
    notes: order.notes ?? "",
  });
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () =>
      updateOrder(order.id, {
        supplier_name: form.supplier_name.trim() || null,
        platform_order_ref: form.platform_order_ref.trim() || null,
        currency: form.currency.trim() || null,
        shipping_cost: form.shipping_cost.trim() || null,
        tracking_number: form.tracking_number.trim() || null,
        tracking_url: form.tracking_url.trim() || null,
        expected_delivery_at: form.expected_delivery_at
          ? `${form.expected_delivery_at}T00:00:00Z`
          : null,
        notes: form.notes.trim() || null,
      }),
    onSuccess: () => {
      toast.success("Order updated");
      void queryClient.invalidateQueries({ queryKey: procurementQueries.all() });
      setOpen(false);
    },
    onError: (error) => toast.error("Could not update order", { description: error.message }),
  });
  const set =
    (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((f) => ({ ...f, [key]: e.target.value }));
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Pencil /> Edit
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Edit {order.order_number}</DialogTitle>
          <DialogDescription>Supplier, costs, tracking and notes.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          {(
            [
              ["supplier_name", "Supplier / shop"],
              ["platform_order_ref", "Platform order ref"],
              ["currency", "Currency"],
              ["shipping_cost", "Shipping cost"],
              ["tracking_number", "Tracking number"],
              ["tracking_url", "Tracking URL"],
            ] as const
          ).map(([key, label]) => (
            <div key={key} className="space-y-1.5">
              <Label htmlFor={`edit-${key}`}>{label}</Label>
              <Input id={`edit-${key}`} value={form[key]} onChange={set(key)} />
            </div>
          ))}
          <div className="space-y-1.5">
            <Label htmlFor="edit-expected">Expected delivery</Label>
            <Input
              id="edit-expected"
              type="date"
              value={form.expected_delivery_at}
              onChange={set("expected_delivery_at")}
            />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="edit-notes">Notes</Label>
            <Textarea id="edit-notes" rows={3} value={form.notes} onChange={set("notes")} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Detail({ order }: { order: OrderDetailType }) {
  const queryClient = useQueryClient();
  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: procurementQueries.all() });
  const [received, setReceived] = useState<Record<string, string>>(() =>
    Object.fromEntries(order.items.map((i) => [i.id, String(i.quantity_received)])),
  );
  const [receiveNote, setReceiveNote] = useState("");
  const status = useMutation({
    mutationFn: (next: PurchaseOrderStatus) => changeOrderStatus(order.id, next),
    onSuccess: (o) => {
      toast.success(`Order ${ORDER_LABEL[o.status].toLowerCase()}`);
      invalidate();
    },
    onError: (error) =>
      toast.error("Status not changed", {
        description: error instanceof ApiError ? error.message : String(error),
      }),
  });
  const receive = useMutation({
    mutationFn: () =>
      receiveOrder(order.id, {
        items: order.items.map((i) => ({
          item_id: i.id,
          quantity: Number(received[i.id] ?? 0) || 0,
        })),
        note: receiveNote.trim() || null,
      }),
    onSuccess: (o) => {
      toast.success(`Received quantities saved · ${ORDER_LABEL[o.status]}`);
      setReceiveNote("");
      invalidate();
    },
    onError: (error) => toast.error("Could not record receipt", { description: error.message }),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title={order.order_number}
        description={
          [order.supplier_name, order.platform_order_ref].filter(Boolean).join(" · ") || undefined
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <OrderStatusBadge status={order.status} />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={order.allowed_transitions.length === 0 || status.isPending}
                >
                  Change status <ChevronDown />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {order.allowed_transitions.map((s) => (
                  <DropdownMenuItem key={s} onClick={() => status.mutate(s)}>
                    {ORDER_LABEL[s]}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
            <EditOrderDialog key={order.updated_at} order={order} />
            <Button asChild variant="ghost" size="sm">
              <Link href="/purchases">
                <ArrowLeft /> Purchases
              </Link>
            </Button>
          </div>
        }
      />

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Order</CardTitle>
          </CardHeader>
          <CardContent>
            <dl>
              <Row label="Platform" value={<span className="capitalize">{order.platform}</span>} />
              <Row label="Supplier" value={order.supplier_name} />
              <Row label="Platform order ref" value={order.platform_order_ref} />
              <Row
                label="Ordered"
                value={order.ordered_at ? formatDateTime(order.ordered_at) : "—"}
              />
              <Row
                label="Expected delivery"
                value={
                  order.expected_delivery_at ? formatDateTime(order.expected_delivery_at) : "—"
                }
              />
              <Row
                label="Delivered"
                value={order.delivered_at ? formatDateTime(order.delivered_at) : "—"}
              />
              <Row
                label="Tracking"
                value={
                  order.tracking_url ? (
                    <a
                      href={order.tracking_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-brand hover:underline"
                    >
                      {order.tracking_number ?? "open"} <ExternalLink className="size-3" />
                    </a>
                  ) : (
                    (order.tracking_number ?? "—")
                  )
                }
              />
              <Row label="Shipping" value={formatMoney(order.shipping_cost, order.currency)} />
              <Row
                label="Total"
                value={
                  <span className="font-semibold">
                    {formatMoney(order.total_cost, order.currency)}
                  </span>
                }
              />
            </dl>
            {order.notes ? (
              <p className="mt-3 text-xs whitespace-pre-wrap text-muted-foreground">
                {order.notes}
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <PackageCheck className="size-4 text-brand" /> Lines and receiving
            </CardTitle>
            <CardDescription>
              {order.receivable
                ? "Enter the cumulative quantity received per line, then save. Status updates automatically."
                : `Receiving is available once the order is placed (current: ${ORDER_LABEL[order.status].toLowerCase()}).`}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {order.items.length === 0 ? (
              <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
                No lines yet. Create a request, approve it, and add it from the Purchases page.
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Product</TableHead>
                    <TableHead className="text-right">Ordered</TableHead>
                    <TableHead className="text-right">Unit cost</TableHead>
                    <TableHead className="text-right">Line</TableHead>
                    <TableHead className="w-28 text-right">Received</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {order.items.map((item) => (
                    <TableRow key={item.id}>
                      <TableCell>
                        <Link
                          href={`/products/${item.product.id}`}
                          className="font-medium hover:text-brand hover:underline"
                        >
                          {item.product.name}
                        </Link>
                        {item.notes ? (
                          <span className="block text-xs text-muted-foreground">{item.notes}</span>
                        ) : null}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {item.quantity_ordered}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {formatMoney(item.unit_cost, order.currency)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {formatMoney(item.line_total, order.currency)}
                      </TableCell>
                      <TableCell className="text-right">
                        {order.receivable ? (
                          <Input
                            type="number"
                            min={0}
                            max={item.quantity_ordered}
                            className="ml-auto w-20 text-right font-mono"
                            value={received[item.id] ?? "0"}
                            onChange={(e) =>
                              setReceived((r) => ({ ...r, [item.id]: e.target.value }))
                            }
                            aria-label={`Received quantity for ${item.product.name}`}
                          />
                        ) : (
                          <span className="font-mono text-xs">{item.quantity_received}</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {order.receivable && order.items.length > 0 ? (
              <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                <div className="flex-1 space-y-1.5">
                  <Label htmlFor="receive-note">Receiving note</Label>
                  <Input
                    id="receive-note"
                    value={receiveNote}
                    onChange={(e) => setReceiveNote(e.target.value)}
                    placeholder="e.g. box 1 of 2 arrived, 1 unit damaged"
                  />
                </div>
                <Button disabled={receive.isPending} onClick={() => receive.mutate()}>
                  {receive.isPending ? <LoaderCircle className="animate-spin" /> : <PackageCheck />}
                  Record received
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export function OrderDetailRoute() {
  const params = useParams<{ id: string }>();
  const query = useQuery(procurementQueries.order(params.id));
  if (query.isPending) return <Skeleton className="h-96 rounded-xl" aria-busy />;
  if (query.isError) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <div className="space-y-4">
        <Alert variant={notFound ? "default" : "destructive"}>
          <TriangleAlert />
          <AlertTitle>{notFound ? "Order not found" : "Could not load order"}</AlertTitle>
          <AlertDescription>{query.error.message}</AlertDescription>
        </Alert>
        <Button asChild variant="outline">
          <Link href="/purchases">
            <ArrowLeft /> Back to purchases
          </Link>
        </Button>
      </div>
    );
  }
  return <Detail order={query.data} />;
}
