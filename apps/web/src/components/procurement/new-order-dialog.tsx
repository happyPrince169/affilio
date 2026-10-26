"use client";

import type { PurchaseRequest } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

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
import { createOrder, procurementQueries } from "@/lib/api/procurement";

const orNull = (v: string) => (v.trim() ? v.trim() : null);

export function NewOrderDialog({ trigger }: { trigger: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Record<string, { checked: boolean; unit_cost: string }>>(
    {},
  );
  const [supplier, setSupplier] = useState("");
  const [platformRef, setPlatformRef] = useState("");
  const [currency, setCurrency] = useState("VND");
  const [shipping, setShipping] = useState("");
  const [tracking, setTracking] = useState("");
  const [trackingUrl, setTrackingUrl] = useState("");
  const [notes, setNotes] = useState("");
  const router = useRouter();
  const queryClient = useQueryClient();
  const approved = useQuery({ ...procurementQueries.requests("approved"), enabled: open });

  const chosen = (approved.data ?? []).filter((r) => selected[r.id]?.checked);
  const mutation = useMutation({
    mutationFn: () =>
      createOrder({
        supplier_name: orNull(supplier),
        platform_order_ref: orNull(platformRef),
        currency: orNull(currency),
        shipping_cost: orNull(shipping),
        tracking_number: orNull(tracking),
        tracking_url: orNull(trackingUrl),
        notes: orNull(notes),
        items: chosen.map((r) => ({
          purchase_request_id: r.id,
          unit_cost: orNull(selected[r.id]?.unit_cost ?? ""),
        })),
      }),
    onSuccess: (order) => {
      toast.success(`Order ${order.order_number} created`);
      void queryClient.invalidateQueries({ queryKey: procurementQueries.all() });
      setOpen(false);
      router.push(`/purchases/orders/${order.id}`);
    },
    onError: (error) => toast.error("Could not create order", { description: error.message }),
  });

  const toggle = (request: PurchaseRequest, checked: boolean) =>
    setSelected((s) => ({
      ...s,
      [request.id]: { checked, unit_cost: s[request.id]?.unit_cost ?? "" },
    }));

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New purchase order</DialogTitle>
          <DialogDescription>
            Pick approved requests to put on this order. Each request can be ordered once; an order
            starts as a draft until you place it.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <p className="mb-2 text-sm font-medium">Approved requests</p>
            {approved.isPending ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : approved.isError ? (
              <p className="text-sm text-destructive">{approved.error.message}</p>
            ) : approved.data.length === 0 ? (
              <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                No approved requests waiting. Approve requests first, or add free lines on the order
                page afterwards.
              </p>
            ) : (
              <ul className="divide-y rounded-md border">
                {approved.data.map((request) => (
                  <li key={request.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                    <Checkbox
                      checked={selected[request.id]?.checked ?? false}
                      onCheckedChange={(v) => toggle(request, v === true)}
                      aria-label={`Include ${request.product.name}`}
                    />
                    <span className="min-w-0 flex-1 truncate">
                      {request.product.name}{" "}
                      <span className="text-muted-foreground">× {request.quantity}</span>
                    </span>
                    <Input
                      className="w-32"
                      placeholder="Unit cost"
                      inputMode="decimal"
                      value={selected[request.id]?.unit_cost ?? ""}
                      disabled={!selected[request.id]?.checked}
                      onChange={(e) =>
                        setSelected((s) => ({
                          ...s,
                          [request.id]: { checked: true, unit_cost: e.target.value },
                        }))
                      }
                      aria-label={`Unit cost for ${request.product.name}`}
                    />
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="po-supplier">Supplier / shop</Label>
              <Input
                id="po-supplier"
                value={supplier}
                onChange={(e) => setSupplier(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="po-ref">Platform order ref</Label>
              <Input
                id="po-ref"
                value={platformRef}
                onChange={(e) => setPlatformRef(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="po-currency">Currency</Label>
              <Input
                id="po-currency"
                maxLength={3}
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="po-shipping">Shipping cost</Label>
              <Input
                id="po-shipping"
                inputMode="decimal"
                value={shipping}
                onChange={(e) => setShipping(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="po-tracking">Tracking number</Label>
              <Input
                id="po-tracking"
                value={tracking}
                onChange={(e) => setTracking(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="po-tracking-url">Tracking URL</Label>
              <Input
                id="po-tracking-url"
                placeholder="https://…"
                value={trackingUrl}
                onChange={(e) => setTrackingUrl(e.target.value)}
              />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="po-notes">Notes</Label>
              <Textarea
                id="po-notes"
                rows={2}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            Create draft order
            {chosen.length ? ` (${chosen.length} line${chosen.length === 1 ? "" : "s"})` : ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
