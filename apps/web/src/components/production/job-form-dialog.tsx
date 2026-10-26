"use client";

import type { ProductionJobDetail, ProductSummary } from "@affilio/shared-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { ProductSearchSelect } from "@/components/products/product-search-select";
import { Badge } from "@/components/ui/badge";
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
import { createJob, productionQueries, updateJob } from "@/lib/api/production";

const orNull = (v: string) => (v.trim() ? v.trim() : null);

export function JobFormDialog({ trigger, job }: { trigger: ReactNode; job?: ProductionJobDetail }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(job?.title ?? "");
  const [description, setDescription] = useState(job?.description ?? "");
  const [planned, setPlanned] = useState(job?.planned_filming_date ?? "");
  const [deadline, setDeadline] = useState(job?.deadline ?? "");
  const [staff, setStaff] = useState((job?.staff_labels ?? []).join(", "));
  const [notes, setNotes] = useState(job?.notes ?? "");
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [picking, setPicking] = useState<ProductSummary | null>(null);
  const router = useRouter();
  const queryClient = useQueryClient();
  const editing = Boolean(job);

  const mutation = useMutation({
    mutationFn: () => {
      const base = {
        title: title.trim(),
        description: orNull(description),
        planned_filming_date: orNull(planned),
        deadline: orNull(deadline),
        staff_labels: staff
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        notes: orNull(notes),
      };
      return job
        ? updateJob(job.id, base)
        : createJob({ ...base, product_ids: products.map((p) => p.id) });
    },
    onSuccess: (saved) => {
      toast.success(editing ? "Job updated" : `Job ${saved.job_number} created`);
      void queryClient.invalidateQueries({ queryKey: productionQueries.all() });
      setOpen(false);
      if (!editing) router.push(`/production/${saved.id}`);
    },
    onError: (error) =>
      toast.error(editing ? "Could not update job" : "Could not create job", {
        description: error.message,
      }),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{editing ? `Edit ${job?.job_number}` : "New production job"}</DialogTitle>
          <DialogDescription>
            A filming session with real staff. Products and videos are optional and can be attached
            later; a job may use several purchased products and produce several videos.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="job-title">Title *</Label>
            <Input
              id="job-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              autoFocus
            />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="job-description">Description / shot list</Label>
            <Textarea
              id="job-description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="job-planned">Planned filming date</Label>
            <Input
              id="job-planned"
              type="date"
              value={planned}
              onChange={(e) => setPlanned(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="job-deadline">Deadline (edit done)</Label>
            <Input
              id="job-deadline"
              type="date"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
            />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="job-staff">Staff (comma separated labels)</Label>
            <Input
              id="job-staff"
              placeholder="Linh (talent), Minh (camera), Hà (editor)"
              value={staff}
              onChange={(e) => setStaff(e.target.value)}
            />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="job-notes">Notes</Label>
            <Textarea
              id="job-notes"
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
          {!editing ? (
            <div className="space-y-2 sm:col-span-2">
              <Label>Products used on set (optional)</Label>
              {products.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {products.map((p) => (
                    <Badge key={p.id} variant="secondary" className="gap-1">
                      {p.name}
                      <button
                        type="button"
                        aria-label={`Remove ${p.name}`}
                        onClick={() => setProducts((ps) => ps.filter((x) => x.id !== p.id))}
                      >
                        <X className="size-3" />
                      </button>
                    </Badge>
                  ))}
                </div>
              ) : null}
              <ProductSearchSelect
                value={picking}
                exclude={new Set(products.map((p) => p.id))}
                onChange={(p) => {
                  setPicking(null);
                  if (p && !products.some((x) => x.id === p.id)) setProducts((ps) => [...ps, p]);
                }}
              />
            </div>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={!title.trim() || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            {editing ? "Save" : "Create job"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
