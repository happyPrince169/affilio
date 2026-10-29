"use client";

import type { ContentCategory, OwnerMapping } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, X } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { createOwnerMapping, ownerMappingQueries, revokeOwnerMapping } from "@/lib/api/media";
import { taxonomyQueries } from "@/lib/api/taxonomy";

/** Active values under the production-owner facet root (never the root itself). */
export function ownerValues(categories: ContentCategory[]): ContentCategory[] {
  return categories
    .filter((c) => c.facet === "production_owner" && c.parent_id !== null && c.status === "active")
    .sort((a, b) => a.name.localeCompare(b.name));
}

interface MemberOwnerMappingsProps {
  userId: string;
  /** Admins see the add/revoke controls; everyone else sees nothing (the list is admin-only). */
  canManage: boolean;
}

/**
 * Production owners a member may submit videos for (upload track U2). One shared query for
 * the whole organization, filtered per member row, so the members list stays one request.
 */
export function MemberOwnerMappings({ userId, canManage }: MemberOwnerMappingsProps) {
  const queryClient = useQueryClient();
  const mappings = useQuery({ ...ownerMappingQueries.list(), enabled: canManage });
  const categories = useQuery({ ...taxonomyQueries.categories(), enabled: canManage });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ownerMappingQueries.all() });

  const assign = useMutation({
    mutationFn: (categoryId: string) =>
      createOwnerMapping({ user_id: userId, production_owner_category_id: categoryId }),
    onSuccess: (m) => {
      toast.success(`Production owner ${m.production_owner.name} assigned`);
      void invalidate();
    },
    onError: (error) => toast.error("Could not assign owner", { description: error.message }),
  });
  const revoke = useMutation({
    mutationFn: (m: OwnerMapping) => revokeOwnerMapping(m.id),
    onSuccess: (_, m) => {
      toast.success(`Production owner ${m.production_owner.name} revoked`);
      void invalidate();
    },
    onError: (error) => toast.error("Could not revoke owner", { description: error.message }),
  });

  if (!canManage) return null;

  const mine = (mappings.data ?? []).filter((m) => m.user_id === userId);
  const mapped = new Set(mine.map((m) => m.production_owner.id));
  const available = ownerValues(categories.data ?? []).filter((c) => !mapped.has(c.id));
  const facetsReady = (categories.data ?? []).some(
    (c) => c.facet === "production_owner" && c.parent_id === null,
  );

  return (
    <div className="mt-1 flex flex-wrap items-center gap-1" data-testid="owner-mappings">
      <span className="text-xs text-muted-foreground">Submits for:</span>
      {mappings.isPending ? (
        <LoaderCircle className="size-3 animate-spin text-muted-foreground" />
      ) : mine.length === 0 ? (
        <span className="text-xs text-muted-foreground">no production owner</span>
      ) : (
        mine.map((m) => {
          const archived = m.production_owner.status === "archived";
          return (
            <Badge
              key={m.id}
              variant={archived ? "outline" : "secondary"}
              className="gap-1 pr-1"
              title={archived ? "Archived owner: not submittable until restored" : undefined}
            >
              {m.production_owner.name}
              {archived ? <span className="text-muted-foreground">(archived)</span> : null}
              <button
                type="button"
                className="rounded-sm hover:bg-muted"
                aria-label={`Revoke ${m.production_owner.name}`}
                onClick={() => revoke.mutate(m)}
                disabled={revoke.isPending}
              >
                <X className="size-3" />
              </button>
            </Badge>
          );
        })
      )}
      {facetsReady ? (
        <Select value="" onValueChange={(id) => assign.mutate(id)} disabled={assign.isPending}>
          <SelectTrigger className="h-7 w-36 text-xs" size="sm" aria-label="Add production owner">
            <SelectValue placeholder={available.length ? "Add owner…" : "All owners assigned"} />
          </SelectTrigger>
          <SelectContent>
            {available.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <Button variant="link" size="sm" className="h-auto p-0 text-xs" asChild>
          <span title="Enable facets under Settings → Categories to create production owners">
            no facet roots yet
          </span>
        </Button>
      )}
    </div>
  );
}
