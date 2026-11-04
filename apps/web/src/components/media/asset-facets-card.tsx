"use client";

import type { CategoryFacet, MediaAssetDetail } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Tags } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { FACET_LABEL, SOURCE_LABEL } from "@/components/taxonomy/category-badges";
import { FacetSelect } from "@/components/taxonomy/facet-select";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { mediaQueries, setAssetFacets } from "@/lib/api/media";
import { uploadQueries } from "@/lib/api/uploads";

const FACETS: CategoryFacet[] = ["production_owner", "product_category", "content_type"];

/**
 * Owner / product category / content type of a final, changed as metadata only: the file
 * stays where it is (fast track F2). Admins change any final; an editor changes finals they
 * uploaded and may pick only owners they are mapped to (the API enforces both).
 */
export function AssetFacetsCard({ asset }: { asset: MediaAssetDetail }) {
  const { can, user } = useAuth();
  const queryClient = useQueryClient();
  const current = Object.fromEntries(
    FACETS.map((facet) => [facet, asset.categories.find((c) => c.facet === facet) ?? null]),
  ) as Record<CategoryFacet, MediaAssetDetail["categories"][number] | null>;
  const isAdmin = can("manage_members");
  const mine = user != null && asset.uploaded_by_user_id === user.id;
  const editable = asset.asset_kind === "final" && (isAdmin || (can("edit") && mine));
  const options = useQuery({ ...uploadQueries.options(), enabled: editable && !isAdmin });
  const allowedOwners =
    !isAdmin && options.data ? new Set(options.data.owners.map((o) => o.id)) : undefined;

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<CategoryFacet, string | null>>({
    production_owner: current.production_owner?.category_id ?? null,
    product_category: current.product_category?.category_id ?? null,
    content_type: current.content_type?.category_id ?? null,
  });
  const changes = {
    production_owner_category_id:
      draft.production_owner !== (current.production_owner?.category_id ?? null)
        ? draft.production_owner
        : undefined,
    product_category_id:
      draft.product_category !== (current.product_category?.category_id ?? null)
        ? draft.product_category
        : undefined,
    content_type_id:
      draft.content_type !== (current.content_type?.category_id ?? null)
        ? draft.content_type
        : undefined,
  };
  const dirty = Object.values(changes).some((v) => v);
  const mutation = useMutation({
    mutationFn: () => setAssetFacets(asset.id, changes),
    onSuccess: (saved) => {
      toast.success("Classification updated", {
        description: "Metadata only: the file was not moved.",
      });
      queryClient.setQueryData(mediaQueries.asset(asset.id).queryKey, saved);
      void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
      setEditing(false);
    },
    onError: (error) => toast.error("Could not reclassify", { description: error.message }),
  });
  if (asset.asset_kind !== "final") return null;
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <Tags className="size-4" /> Owner, product category, content type
          </CardTitle>
          <CardDescription>
            {asset.ingest_source === "upload"
              ? "Chosen at upload and kept only in the database: changing them never moves the file."
              : "Derived from the legacy folder path. A manual change wins over the folders and survives rescans."}
          </CardDescription>
        </div>
        {editable && !editing ? (
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            Reclassify
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        <dl className="grid gap-2 sm:grid-cols-3">
          {FACETS.map((facet) => (
            <div key={facet} className="rounded-lg border p-2">
              <dt className="text-xs text-muted-foreground">{FACET_LABEL[facet]}</dt>
              <dd className="flex flex-wrap items-center gap-1 text-sm font-medium">
                {current[facet]?.name ?? <span className="text-muted-foreground">Not set</span>}
                {current[facet] ? (
                  <Badge variant="outline" className="text-[10px]">
                    {SOURCE_LABEL[current[facet]!.assignment_source]}
                  </Badge>
                ) : null}
              </dd>
            </div>
          ))}
        </dl>
        {editing ? (
          <div className="space-y-3 rounded-lg border p-3">
            <div className="grid gap-3 sm:grid-cols-3">
              {FACETS.map((facet) => (
                <div key={facet} className="space-y-1.5">
                  <Label>{FACET_LABEL[facet]}</Label>
                  <FacetSelect
                    facet={facet}
                    value={draft[facet]}
                    onChange={(id) => setDraft({ ...draft, [facet]: id })}
                    noneLabel="Keep unchanged"
                    only={facet === "production_owner" ? allowedOwners : undefined}
                    className="w-full"
                  />
                </div>
              ))}
            </div>
            {!isAdmin ? (
              <p className="text-xs text-muted-foreground">
                You can pick only production owners you are mapped to.
              </p>
            ) : null}
            <div className="flex gap-2">
              <Button onClick={() => mutation.mutate()} disabled={!dirty || mutation.isPending}>
                {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Save
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
