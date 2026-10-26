"use client";

import type { ContentCategory } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArchiveRestore,
  LoaderCircle,
  Pencil,
  Plus,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { CategorySelect } from "@/components/taxonomy/category-select";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api/client";
import { classificationQueries } from "@/lib/api/media";
import {
  createCategory,
  deleteCategory,
  ensureUncategorized,
  taxonomyQueries,
  updateCategory,
} from "@/lib/api/taxonomy";

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    const details = error.details as
      { references?: Record<string, number>; active_children?: number } | undefined;
    if (details?.references) {
      const refs = Object.entries(details.references)
        .map(([t, n]) => `${n} in ${t}`)
        .join(", ");
      return `${error.message} (${refs})`;
    }
  }
  return error instanceof Error ? error.message : String(error);
}

function CategoryDialog({
  trigger,
  category,
  parentId,
}: {
  trigger: ReactNode;
  category?: ContentCategory;
  parentId?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(category?.name ?? "");
  const [slug, setSlug] = useState(category?.slug ?? "");
  const [parent, setParent] = useState<string | null>(category?.parent_id ?? parentId ?? null);
  const [description, setDescription] = useState(category?.description ?? "");
  const [sortOrder, setSortOrder] = useState(String(category?.sort_order ?? 0));
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () =>
      category
        ? updateCategory(category.id, {
            name: name.trim(),
            slug: slug.trim() || undefined,
            parent_id: parent,
            description: description.trim() || null,
            sort_order: Number(sortOrder) || 0,
          })
        : createCategory({
            name: name.trim(),
            slug: slug.trim() || null,
            parent_id: parent,
            description: description.trim() || null,
            sort_order: Number(sortOrder) || 0,
          }),
    onSuccess: () => {
      toast.success(category ? "Category updated" : "Category created");
      void queryClient.invalidateQueries({ queryKey: taxonomyQueries.all() });
      setOpen(false);
    },
    onError: (error) => toast.error("Could not save category", { description: describe(error) }),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{category ? `Edit ${category.name}` : "New category"}</DialogTitle>
          <DialogDescription>
            Sibling slugs must be unique. Moving a category under its own descendant is refused.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="cat-name">Name</Label>
            <Input id="cat-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="cat-slug">Slug (optional)</Label>
              <Input
                id="cat-slug"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                placeholder="auto from name"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cat-sort">Sort order</Label>
              <Input
                id="cat-sort"
                type="number"
                min={0}
                value={sortOrder}
                onChange={(e) => setSortOrder(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>Parent</Label>
            <CategorySelect
              value={parent}
              onChange={setParent}
              noneLabel="Top level"
              exclude={category ? new Set([category.id]) : undefined}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cat-desc">Description</Label>
            <Textarea
              id="cat-desc"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending || !name.trim()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function CategoriesManager() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);
  const categories = useQuery(taxonomyQueries.categories(showArchived));
  const counts = useQuery(classificationQueries.counts());
  const countById = new Map((counts.data ?? []).map((c) => [c.category_id, c.primary_count]));
  const invalidate = () => queryClient.invalidateQueries({ queryKey: taxonomyQueries.all() });
  const archive = useMutation({
    mutationFn: (c: ContentCategory) =>
      updateCategory(c.id, { status: c.status === "archived" ? "active" : "archived" }),
    onSuccess: () => void invalidate(),
    onError: (error) => toast.error("Could not change status", { description: describe(error) }),
  });
  const remove = useMutation({
    mutationFn: (c: ContentCategory) => deleteCategory(c.id),
    onSuccess: () => {
      toast.success("Category deleted");
      void invalidate();
    },
    onError: (error) => toast.error("Could not delete", { description: describe(error) }),
  });
  const fallback = useMutation({
    mutationFn: ensureUncategorized,
    onSuccess: () => void invalidate(),
    onError: (error) => toast.error("Could not create fallback", { description: describe(error) }),
  });
  const admin = can("manage_accounts");
  const hasFallback = (categories.data ?? []).some(
    (c) => c.slug === "uncategorized" && !c.parent_id,
  );

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>Content categories</CardTitle>
          <CardDescription>
            Hierarchy used to classify final videos and to route them to Pages. Folder rules and
            channel rules reference these categories; referenced categories can only be archived.
          </CardDescription>
        </div>
        {admin ? (
          <div className="flex items-center gap-2">
            {!hasFallback && categories.data ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => fallback.mutate()}
                disabled={fallback.isPending}
              >
                Create “Uncategorized”
              </Button>
            ) : null}
            <CategoryDialog
              trigger={
                <Button size="sm">
                  <Plus /> New category
                </Button>
              }
            />
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
          />{" "}
          Show archived
        </label>
        {categories.isPending ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : categories.isError ? (
          <Alert variant="destructive">
            <TriangleAlert />
            <AlertTitle>Could not load categories</AlertTitle>
            <AlertDescription>{categories.error.message}</AlertDescription>
          </Alert>
        ) : categories.data.length === 0 ? (
          <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            No categories yet. Start with the top-level ones your NAS folders represent (e.g.
            Men&apos;s Sports, Home &amp; Living) and add children like Pickleball or Kitchen.
          </p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {categories.data.map((c) => (
              <li
                key={c.id}
                className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm"
                style={{ paddingLeft: `${12 + c.depth * 20}px` }}
              >
                <span className="min-w-0 flex-1 truncate">
                  <span
                    className={
                      c.status === "archived" ? "text-muted-foreground line-through" : "font-medium"
                    }
                  >
                    {c.name}
                  </span>{" "}
                  <span className="font-mono text-xs text-muted-foreground">{c.slug}</span>
                  {c.description ? (
                    <span className="block truncate text-xs text-muted-foreground">
                      {c.description}
                    </span>
                  ) : null}
                </span>
                <Badge variant="outline" className="text-[10px]">
                  {countById.get(c.id) ?? 0} video{(countById.get(c.id) ?? 0) === 1 ? "" : "s"}
                </Badge>
                {admin ? (
                  <div className="flex items-center gap-1">
                    <CategoryDialog
                      parentId={c.id}
                      trigger={
                        <Button variant="ghost" size="icon-sm" aria-label="Add child">
                          <Plus />
                        </Button>
                      }
                    />
                    <CategoryDialog
                      category={c}
                      trigger={
                        <Button variant="ghost" size="icon-sm" aria-label="Edit">
                          <Pencil />
                        </Button>
                      }
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={c.status === "archived" ? "Restore" : "Archive"}
                      onClick={() => archive.mutate(c)}
                    >
                      {c.status === "archived" ? <ArchiveRestore /> : <Archive />}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Delete"
                      onClick={() => remove.mutate(c)}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
