"use client";

import type { ProductDetail, ProductStatus } from "@affilio/shared-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, LoaderCircle, Trophy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { STATUS_LABEL } from "@/components/products/product-badges";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api/client";
import { changeProductStatus, productQueries } from "@/lib/api/products";

export function ProductStatusControl({ product }: { product: ProductDetail }) {
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<ProductStatus | null>(null);
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  const mutation = useMutation({
    mutationFn: (next: { status: ProductStatus; note?: string; confirmed?: boolean }) =>
      changeProductStatus(product.id, next),
    onSuccess: (saved) => {
      toast.success(`Status set to ${STATUS_LABEL[saved.status]}`);
      void queryClient.invalidateQueries({ queryKey: productQueries.all() });
      setTarget(null);
      setNote("");
      setConfirmed(false);
    },
    onError: (error) => {
      toast.error("Status not changed", {
        description:
          error instanceof ApiError && error.code === "winner_confirmation_required"
            ? "Winner needs an explicit confirmation and a note describing the evidence."
            : error.message,
      });
    },
  });

  const isWinner = target === "winner";
  const canSubmit = !isWinner || (confirmed && note.trim().length > 0);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" disabled={product.allowed_transitions.length === 0}>
            Change status <ChevronDown />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {product.allowed_transitions.map((status) => (
            <DropdownMenuItem key={status} onClick={() => setTarget(status)}>
              {status === "winner" ? <Trophy className="text-success" /> : null}
              {STATUS_LABEL[status]}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={target !== null} onOpenChange={(open) => !open && setTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {isWinner ? "Confirm winner" : `Move to ${target ? STATUS_LABEL[target] : ""}`}
            </DialogTitle>
            <DialogDescription>
              {isWinner
                ? "WINNER is a human decision, never automatic. Record the evidence you are basing it on (orders, commission, conversion) so it can be audited later."
                : `From ${STATUS_LABEL[product.status]} to ${target ? STATUS_LABEL[target] : ""}. An optional note is stored with the change.`}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="status-note">{isWinner ? "Evidence (required)" : "Note"}</Label>
              <Textarea
                id="status-note"
                rows={3}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder={
                  isWinner ? "e.g. 3 videos, 42 orders, 1.9M VND commission in 14 days" : ""
                }
              />
            </div>
            {isWinner ? (
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1 accent-brand"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                <span>
                  I confirm this product is a winner based on observed results, not on sparse or
                  estimated data.
                </span>
              </label>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTarget(null)}>
              Cancel
            </Button>
            <Button
              disabled={!canSubmit || mutation.isPending}
              onClick={() =>
                target &&
                mutation.mutate({ status: target, note: note.trim() || undefined, confirmed })
              }
            >
              {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
              {isWinner ? "Confirm winner" : "Change status"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
