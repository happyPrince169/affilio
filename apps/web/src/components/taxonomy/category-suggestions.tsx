"use client";

import type { CategorySuggestion, CategorySuggestionStatus } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FolderSearch, LoaderCircle, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { FACET_LABEL } from "@/components/taxonomy/category-badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  approveCategorySuggestion,
  classificationQueries,
  mediaQueries,
  rejectCategorySuggestion,
} from "@/lib/api/media";
import { taxonomyQueries } from "@/lib/api/taxonomy";
import { formatDateTime } from "@/lib/format";

const STATUS_LABEL: Record<CategorySuggestionStatus, string> = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
};

function SuggestionRow({
  suggestion,
  admin,
  onDone,
}: {
  suggestion: CategorySuggestion;
  admin: boolean;
  onDone: () => void;
}) {
  const [name, setName] = useState("");
  const approve = useMutation({
    mutationFn: () => approveCategorySuggestion(suggestion.id, { name: name.trim() || null }),
    onSuccess: (decision) => {
      toast.success(`Category created`, {
        description: `${decision.reclassified_assets} video${
          decision.reclassified_assets === 1 ? "" : "s"
        } classified`,
      });
      onDone();
    },
    onError: (error) => toast.error("Could not approve", { description: error.message }),
  });
  const reject = useMutation({
    mutationFn: () => rejectCategorySuggestion(suggestion.id, {}),
    onSuccess: () => {
      toast.success("Suggestion rejected");
      onDone();
    },
    onError: (error) => toast.error("Could not reject", { description: error.message }),
  });
  const pending = suggestion.status === "pending";
  const busy = approve.isPending || reject.isPending;
  return (
    <li className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
      <FolderSearch className="size-4 text-muted-foreground" />
      <Badge variant="outline" className="text-[10px]">
        {FACET_LABEL[suggestion.facet]}
      </Badge>
      <span className="font-medium">{suggestion.name}</span>
      <span className="font-mono text-xs text-muted-foreground">
        {suggestion.relative_folder_path}
      </span>
      <span className="text-xs text-muted-foreground">
        {suggestion.seen_count} video{suggestion.seen_count === 1 ? "" : "s"} · last seen{" "}
        {formatDateTime(suggestion.last_seen_at)}
      </span>
      {!pending ? (
        <Badge variant="secondary" className="text-[10px]">
          {STATUS_LABEL[suggestion.status]}
          {suggestion.category_name ? ` → ${suggestion.category_name}` : ""}
        </Badge>
      ) : null}
      {admin && pending ? (
        <span className="ml-auto flex items-center gap-1">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={suggestion.name.replace(/[-_]+/g, " ")}
            className="h-8 w-44"
            aria-label={`Category name for ${suggestion.name}`}
            disabled={busy}
          />
          <Button size="sm" onClick={() => approve.mutate()} disabled={busy}>
            {approve.isPending ? <LoaderCircle className="animate-spin" /> : <Check />} Approve
          </Button>
          <Button variant="ghost" size="sm" onClick={() => reject.mutate()} disabled={busy}>
            <X /> Reject
          </Button>
        </span>
      ) : null}
    </li>
  );
}

/** Facet values the scanner found in finals folders that match no category yet. Nothing is
 * created until an admin approves; approval also classifies the videos already indexed. */
export function CategorySuggestions() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [showDecided, setShowDecided] = useState(false);
  const suggestions = useQuery(
    classificationQueries.suggestions(showDecided ? undefined : "pending"),
  );
  const admin = can("manage_accounts");
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: classificationQueries.all() });
    void queryClient.invalidateQueries({ queryKey: taxonomyQueries.all() });
    void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
  };
  const rows = suggestions.data ?? [];
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle className="text-base">New folders found by the scanner</CardTitle>
          <CardDescription>
            Finals under <span className="font-mono">Owner/ProductCategory/ContentType/</span> name
            a value that is not a category yet. Approving creates the category and classifies those
            videos; rejecting hides the folder until it is renamed. Files are never touched.
          </CardDescription>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={showDecided}
            onChange={(e) => setShowDecided(e.target.checked)}
          />{" "}
          Show decided
        </label>
      </CardHeader>
      <CardContent>
        {suggestions.isPending ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : suggestions.isError ? (
          <p className="text-sm text-destructive">{suggestions.error.message}</p>
        ) : rows.length === 0 ? (
          <p className="rounded-lg border border-dashed p-4 text-center text-sm text-muted-foreground">
            No pending suggestions. New folders appear here after the next finals scan.
          </p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {rows.map((s) => (
              <SuggestionRow key={s.id} suggestion={s} admin={admin} onDone={refresh} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
