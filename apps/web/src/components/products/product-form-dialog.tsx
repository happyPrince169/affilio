"use client";

import type { ProductCreate, ProductDetail, ProductUpdate } from "@affilio/shared-types";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useEffect, type ReactNode, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

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
import { ApiError } from "@/lib/api/client";
import { createProduct, productQueries, updateProduct } from "@/lib/api/products";

const httpUrl = z
  .string()
  .trim()
  .max(2048)
  .refine((v) => v === "" || /^https?:\/\/[^\s/?#]+/i.test(v), "Must be an http(s) URL")
  .optional();

const decimalString = (max: number, message: string) =>
  z
    .string()
    .trim()
    .refine((v) => v === "" || (/^\d+(\.\d+)?$/.test(v) && Number(v) <= max), message)
    .optional();

const schema = z.object({
  name: z.string().trim().min(1, "Name is required").max(255),
  product_url: httpUrl,
  affiliate_url: httpUrl,
  platform_product_id: z.string().trim().max(128).optional(),
  seller_name: z.string().trim().max(255).optional(),
  category: z.string().trim().max(120).optional(),
  images: z.string().optional(),
  currency: z
    .string()
    .trim()
    .refine((v) => v === "" || /^[A-Za-z]{3}$/.test(v), "3-letter ISO code")
    .optional(),
  observed_price: decimalString(1e12, "Enter a positive amount"),
  estimated_commission_percent: decimalString(100, "0–100 %"),
  estimated_commission_amount: decimalString(1e12, "Enter a positive amount"),
  research_notes: z.string().max(20_000).optional(),
});

type FormValues = z.infer<typeof schema>;

const EMPTY: FormValues = {
  name: "",
  product_url: "",
  affiliate_url: "",
  platform_product_id: "",
  seller_name: "",
  category: "",
  images: "",
  currency: "VND",
  observed_price: "",
  estimated_commission_percent: "",
  estimated_commission_amount: "",
  research_notes: "",
};

function fromProduct(product: ProductDetail): FormValues {
  return {
    name: product.name,
    product_url: product.product_url ?? "",
    affiliate_url: product.affiliate_url ?? "",
    platform_product_id: product.platform_product_id ?? "",
    seller_name: product.seller_name ?? "",
    category: product.category ?? "",
    images: product.images.join("\n"),
    currency: product.currency ?? "",
    observed_price: product.observed_price ?? "",
    estimated_commission_percent: product.estimated_commission_rate
      ? String(Number(product.estimated_commission_rate) * 100)
      : "",
    estimated_commission_amount: product.estimated_commission_amount ?? "",
    research_notes: product.research_notes ?? "",
  };
}

const orNull = (value: string | undefined) => (value && value.trim() ? value.trim() : null);

function toPayload(values: FormValues): ProductUpdate {
  const percent = orNull(values.estimated_commission_percent);
  return {
    name: values.name.trim(),
    product_url: orNull(values.product_url),
    affiliate_url: orNull(values.affiliate_url),
    platform_product_id: orNull(values.platform_product_id),
    seller_name: orNull(values.seller_name),
    category: orNull(values.category),
    images: (values.images ?? "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean),
    currency: orNull(values.currency)?.toUpperCase() ?? null,
    observed_price: orNull(values.observed_price),
    estimated_commission_rate: percent ? (Number(percent) / 100).toFixed(4) : null,
    estimated_commission_amount: orNull(values.estimated_commission_amount),
    research_notes: orNull(values.research_notes),
  };
}

function Field({
  id,
  label,
  error,
  hint,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

interface ProductFormDialogProps {
  trigger: ReactNode;
  product?: ProductDetail;
  onSaved?: (product: ProductDetail) => void;
}

export function ProductFormDialog({ trigger, product, onSaved }: ProductFormDialogProps) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const editing = Boolean(product);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: product ? fromProduct(product) : EMPTY,
  });

  useEffect(() => {
    if (open) form.reset(product ? fromProduct(product) : EMPTY);
  }, [open, product, form]);

  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      product
        ? updateProduct(product.id, toPayload(values))
        : createProduct(toPayload(values) as ProductCreate),
    onSuccess: (saved) => {
      toast.success(editing ? "Product updated" : "Product created");
      void queryClient.invalidateQueries({ queryKey: productQueries.all() });
      setOpen(false);
      onSaved?.(saved);
    },
    onError: (error) => {
      const message =
        error instanceof ApiError && error.code === "duplicate_product"
          ? "A product with the same platform id or URL already exists."
          : error instanceof ApiError && error.code === "invalid_product_url"
            ? error.message
            : error instanceof ApiError && error.code === "admin_token_required"
              ? "Writes require the admin token (MEDIA_ADMIN_TOKEN on the web server)."
              : error.message;
      toast.error(editing ? "Could not update product" : "Could not create product", {
        description: message,
      });
    },
  });

  const errors = form.formState.errors;
  const reg = form.register;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit product" : "New product"}</DialogTitle>
          <DialogDescription>
            Shopee affiliate product. Leave anything unknown empty; nothing is fetched or guessed.
          </DialogDescription>
        </DialogHeader>
        <form
          id="product-form"
          className="grid gap-4 sm:grid-cols-2"
          onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
        >
          <div className="sm:col-span-2">
            <Field id="name" label="Product name" error={errors.name?.message}>
              <Input id="name" autoFocus {...reg("name")} />
            </Field>
          </div>
          <div className="sm:col-span-2">
            <Field id="product_url" label="Product URL" error={errors.product_url?.message}>
              <Input id="product_url" placeholder="https://shopee.vn/…" {...reg("product_url")} />
            </Field>
          </div>
          <div className="sm:col-span-2">
            <Field
              id="affiliate_url"
              label="Affiliate URL"
              error={errors.affiliate_url?.message}
              hint="Your tracked link, if you already have one."
            >
              <Input
                id="affiliate_url"
                placeholder="https://s.shopee.vn/…"
                {...reg("affiliate_url")}
              />
            </Field>
          </div>
          <Field
            id="platform_product_id"
            label="Platform product ID"
            error={errors.platform_product_id?.message}
            hint="Optional; used for duplicate detection."
          >
            <Input id="platform_product_id" {...reg("platform_product_id")} />
          </Field>
          <Field id="seller_name" label="Seller / shop" error={errors.seller_name?.message}>
            <Input id="seller_name" {...reg("seller_name")} />
          </Field>
          <Field id="category" label="Category" error={errors.category?.message}>
            <Input id="category" {...reg("category")} />
          </Field>
          <Field id="currency" label="Currency" error={errors.currency?.message}>
            <Input id="currency" maxLength={3} {...reg("currency")} />
          </Field>
          <Field id="observed_price" label="Observed price" error={errors.observed_price?.message}>
            <Input id="observed_price" inputMode="decimal" {...reg("observed_price")} />
          </Field>
          <Field
            id="estimated_commission_percent"
            label="Est. commission %"
            error={errors.estimated_commission_percent?.message}
            hint="Estimate only. Actual earnings come from analytics later."
          >
            <Input
              id="estimated_commission_percent"
              inputMode="decimal"
              {...reg("estimated_commission_percent")}
            />
          </Field>
          <Field
            id="estimated_commission_amount"
            label="Est. commission amount"
            error={errors.estimated_commission_amount?.message}
          >
            <Input
              id="estimated_commission_amount"
              inputMode="decimal"
              {...reg("estimated_commission_amount")}
            />
          </Field>
          <div className="sm:col-span-2">
            <Field
              id="images"
              label="Image URLs"
              error={errors.images?.message}
              hint="One http(s) URL per line, up to 10."
            >
              <Textarea id="images" rows={2} {...reg("images")} />
            </Field>
          </div>
          <div className="sm:col-span-2">
            <Field
              id="research_notes"
              label="Research notes"
              error={errors.research_notes?.message}
            >
              <Textarea id="research_notes" rows={4} {...reg("research_notes")} />
            </Field>
          </div>
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} type="button">
            Cancel
          </Button>
          <Button type="submit" form="product-form" disabled={mutation.isPending}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            {editing ? "Save changes" : "Create product"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
