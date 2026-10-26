"use client";

import type { ProductSummary, PurchaseRequest } from "@affilio/shared-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { ProductSearchSelect } from "@/components/products/product-search-select";
import { Button } from "@/components/ui/button";
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
import { createRequest, decideRequest, procurementQueries } from "@/lib/api/procurement";

export function NewRequestDialog({
  trigger,
  product: fixedProduct,
}: {
  trigger: ReactNode;
  product?: ProductSummary;
}) {
  const [open, setOpen] = useState(false);
  const [product, setProduct] = useState<ProductSummary | null>(fixedProduct ?? null);
  const [quantity, setQuantity] = useState("1");
  const [reason, setReason] = useState("");
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () =>
      createRequest({
        product_id: product!.id,
        quantity: Number(quantity) || 1,
        reason: reason || null,
      }),
    onSuccess: () => {
      toast.success("Purchase requested");
      void queryClient.invalidateQueries({ queryKey: procurementQueries.all() });
      setOpen(false);
      setReason("");
      setQuantity("1");
      if (!fixedProduct) setProduct(null);
    },
    onError: (error) => toast.error("Could not create request", { description: error.message }),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Request a purchase</DialogTitle>
          <DialogDescription>
            Ask for units of a product to be bought for filming. A reviewer approves or rejects the
            request; approved requests are placed on purchase orders.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {fixedProduct ? (
            <p className="text-sm">
              Product: <span className="font-medium">{fixedProduct.name}</span>
            </p>
          ) : (
            <ProductSearchSelect value={product} onChange={setProduct} />
          )}
          <div className="grid grid-cols-[6rem_1fr] gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="req-qty">Quantity</Label>
              <Input
                id="req-qty"
                type="number"
                min={1}
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="req-reason">Reason</Label>
              <Textarea
                id="req-reason"
                rows={2}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={!product || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            Request
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function DecisionDialog({
  request,
  decision,
  trigger,
}: {
  request: PurchaseRequest;
  decision: "approved" | "rejected" | "cancelled" | "requested";
  trigger: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => decideRequest(request.id, { status: decision, note: note || null }),
    onSuccess: () => {
      toast.success(`Request ${decision}`);
      void queryClient.invalidateQueries({ queryKey: procurementQueries.all() });
      setOpen(false);
      setNote("");
    },
    onError: (error) => toast.error("Could not update request", { description: error.message }),
  });
  const label = {
    approved: "Approve",
    rejected: "Reject",
    cancelled: "Cancel request",
    requested: "Reopen",
  }[decision];
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {label}: {request.product.name}
          </DialogTitle>
          <DialogDescription>
            {request.quantity} unit{request.quantity === 1 ? "" : "s"}
            {request.reason ? ` · ${request.reason}` : ""}. The decision is recorded with your note.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label htmlFor="decision-note">Note</Label>
          <Textarea
            id="decision-note"
            rows={3}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Back
          </Button>
          <Button
            variant={
              decision === "rejected" || decision === "cancelled" ? "destructive" : "default"
            }
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            {label}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
