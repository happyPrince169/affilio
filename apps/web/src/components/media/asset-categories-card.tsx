"use client";

import type { MediaAssetDetail } from "@affilio/shared-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderTree, LoaderCircle, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { CategoryBadge, SOURCE_LABEL } from "@/components/taxonomy/category-badges";
import { CategorySelect } from "@/components/taxonomy/category-select";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { mediaQueries, setAssetCategories } from "@/lib/api/media";
import { formatDateTime } from "@/lib/format";

export function AssetCategoriesCard({ asset }: { asset: MediaAssetDetail }) {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const primary = asset.categories.find((c) => c.is_primary) ?? null;
  const folderDerived = asset.categories.find((c) => c.assignment_source === "folder") ?? null;
  const manualPrimary =
    primary && primary.assignment_source === "manual" ? primary.category_id : null;
  const [editing, setEditing] = useState(false);
  const [primaryId, setPrimaryId] = useState<string | null>(manualPrimary);
  const [secondary, setSecondary] = useState<string[]>(
    asset.categories
      .filter((c) => !c.is_primary && c.assignment_source !== "folder")
      .map((c) => c.category_id),
  );
  const [adding, setAdding] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () =>
      setAssetCategories(asset.id, {
        primary_category_id: primaryId,
        secondary_category_ids: secondary,
      }),
    onSuccess: (saved) => {
      toast.success("Categories updated");
      queryClient.setQueryData(mediaQueries.asset(asset.id).queryKey, saved);
      void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
      setEditing(false);
    },
    onError: (error) => toast.error("Could not update categories", { description: error.message }),
  });
  if (asset.asset_kind !== "final") return null;
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle className="text-base">Classification</CardTitle>
          <CardDescription>
            Folder <span className="font-mono">{asset.folder_path || "(root)"}</span>
            {folderDerived ? (
              <>
                {" "}
                maps to <span className="font-medium">{folderDerived.path}</span>
              </>
            ) : (
              " has no mapping rule"
            )}
            .
            {asset.classification_changed_at
              ? ` Changed ${formatDateTime(asset.classification_changed_at)}.`
              : ""}
          </CardDescription>
        </div>
        {can("edit") && !editing ? (
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            Override
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        {asset.categories.length === 0 ? (
          <p className="text-sm text-muted-foreground">Not classified yet (scan pending).</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {asset.categories.map((c) => (
              <li key={c.category_id} className="flex flex-wrap items-center gap-2">
                <CategoryBadge category={c} />
                <span className="text-muted-foreground">{c.path}</span>
                <Badge variant="outline" className="text-[10px]">
                  {c.is_primary ? "primary" : "secondary"} · {SOURCE_LABEL[c.assignment_source]}
                </Badge>
              </li>
            ))}
          </ul>
        )}
        {editing ? (
          <div className="space-y-3 rounded-lg border p-3">
            <div className="space-y-1.5">
              <Label>Primary category</Label>
              <CategorySelect
                value={primaryId}
                onChange={setPrimaryId}
                noneLabel={folderDerived ? `Use folder rule (${folderDerived.name})` : "None"}
              />
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <FolderTree className="size-3" /> A manual primary wins over the folder rule and
                survives rescans.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label>Secondary categories</Label>
              <div className="flex flex-wrap gap-1">
                {secondary.map((id) => (
                  <Badge key={id} variant="outline" className="gap-1">
                    {id.slice(0, 8)}…
                    <button
                      type="button"
                      aria-label="Remove"
                      onClick={() => setSecondary(secondary.filter((s) => s !== id))}
                    >
                      <X className="size-3" />
                    </button>
                  </Badge>
                ))}
              </div>
              <div className="flex items-center gap-2">
                <CategorySelect
                  value={adding}
                  onChange={setAdding}
                  placeholder="Add secondary…"
                  className="w-56"
                  exclude={new Set([...secondary, ...(primaryId ? [primaryId] : [])])}
                />
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!adding}
                  onClick={() => {
                    if (adding) setSecondary([...secondary, adding]);
                    setAdding(null);
                  }}
                >
                  Add
                </Button>
              </div>
            </div>
            <div className="flex gap-2">
              <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
                {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Save
                override
              </Button>
              <Button variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
