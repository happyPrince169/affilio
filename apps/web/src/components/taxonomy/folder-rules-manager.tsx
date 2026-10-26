"use client";

import type { FolderRule } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderTree, LoaderCircle, Plus, Trash2, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { CategorySelect } from "@/components/taxonomy/category-select";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  classificationQueries,
  createFolderRule,
  deleteFolderRule,
  mediaQueries,
  updateFolderRule,
} from "@/lib/api/media";

export function FolderRulesManager() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const roots = useQuery(mediaQueries.roots());
  const rules = useQuery(classificationQueries.rules());
  const report = useQuery(classificationQueries.report());
  const finalsRoot = roots.data?.find((r) => r.kind === "final") ?? null;
  const [path, setPath] = useState("");
  const [categoryId, setCategoryId] = useState<string | null>(null);
  const [subfolders, setSubfolders] = useState(true);
  const [priority, setPriority] = useState("0");
  const [previewPath, setPreviewPath] = useState("");
  const preview = useQuery(classificationQueries.preview(finalsRoot?.id ?? null, previewPath));
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: classificationQueries.all() });
    void queryClient.invalidateQueries({ queryKey: mediaQueries.all() });
  };
  const create = useMutation({
    mutationFn: () =>
      createFolderRule({
        media_root_id: finalsRoot!.id,
        relative_folder_path: path,
        category_id: categoryId!,
        include_subfolders: subfolders,
        priority: Number(priority) || 0,
      }),
    onSuccess: () => {
      toast.success("Folder rule created");
      setPath("");
      invalidate();
    },
    onError: (error) => toast.error("Could not create rule", { description: error.message }),
  });
  const toggle = useMutation({
    mutationFn: (rule: FolderRule) => updateFolderRule(rule.id, { is_active: !rule.is_active }),
    onSuccess: invalidate,
    onError: (error) => toast.error("Could not update rule", { description: error.message }),
  });
  const remove = useMutation({
    mutationFn: (rule: FolderRule) => deleteFolderRule(rule.id),
    onSuccess: invalidate,
    onError: (error) => toast.error("Could not delete rule", { description: error.message }),
  });
  const admin = can("manage_accounts");
  const unmapped = (report.data ?? []).filter((r) => !r.mapped);
  const overlapping = new Set<string>();
  for (const a of rules.data ?? [])
    for (const b of rules.data ?? [])
      if (
        a.id !== b.id &&
        a.is_active &&
        b.is_active &&
        a.include_subfolders &&
        (b.relative_folder_path === a.relative_folder_path ||
          b.relative_folder_path.startsWith(`${a.relative_folder_path}/`) ||
          a.relative_folder_path === "")
      )
        overlapping.add(b.id);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Folder → category mapping</CardTitle>
          <CardDescription>
            Final videos take their primary category from the folder they are uploaded into
            (relative to the finals root). The most specific folder wins; ties go to the higher
            priority. Unmapped folders fall back to “Uncategorized”. Source files are never moved.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!finalsRoot ? (
            <Alert>
              <TriangleAlert />
              <AlertTitle>No finals root</AlertTitle>
              <AlertDescription>
                Configure `MEDIA_FINALS_PATH` and reload; rules apply to final roots only.
              </AlertDescription>
            </Alert>
          ) : (
            <p className="text-xs text-muted-foreground">
              Root: <span className="font-mono">{finalsRoot.key}</span>{" "}
              {finalsRoot.configured ? "(configured)" : "(path not configured on this server)"}
            </p>
          )}
          {rules.isPending ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : rules.isError ? (
            <p className="text-sm text-destructive">{rules.error.message}</p>
          ) : rules.data.length === 0 ? (
            <p className="rounded-lg border border-dashed p-4 text-center text-sm text-muted-foreground">
              No rules yet.
            </p>
          ) : (
            <ul className="divide-y rounded-lg border">
              {rules.data.map((rule) => (
                <li key={rule.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
                  <FolderTree className="size-4 text-muted-foreground" />
                  <span className="font-mono">{rule.relative_folder_path || "(root)"}</span>
                  <span className="text-muted-foreground">→</span>
                  <span className="font-medium">{rule.category_path}</span>
                  <Badge variant="outline" className="text-[10px]">
                    {rule.include_subfolders ? "with subfolders" : "exact folder"}
                  </Badge>
                  {rule.priority ? (
                    <Badge variant="outline" className="text-[10px]">
                      priority {rule.priority}
                    </Badge>
                  ) : null}
                  {!rule.is_active ? (
                    <Badge variant="secondary" className="text-[10px]">
                      disabled
                    </Badge>
                  ) : null}
                  {overlapping.has(rule.id) ? (
                    <Badge
                      variant="outline"
                      className="border-warning/60 text-[10px] text-warning"
                      title="A broader rule also matches; the most specific folder wins."
                    >
                      overlaps a parent rule
                    </Badge>
                  ) : null}
                  {admin ? (
                    <span className="ml-auto flex items-center gap-1">
                      <Button variant="ghost" size="sm" onClick={() => toggle.mutate(rule)}>
                        {rule.is_active ? "Disable" : "Enable"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Delete rule"
                        onClick={() => remove.mutate(rule)}
                      >
                        <Trash2 />
                      </Button>
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          {admin && finalsRoot ? (
            <form
              className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (categoryId) create.mutate();
              }}
            >
              <p className="text-sm font-medium sm:col-span-2">Add a rule</p>
              <div className="space-y-1.5">
                <Label htmlFor="rule-path">Folder (relative to the finals root)</Label>
                <Input
                  id="rule-path"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder="mens-sports/pickleball"
                />
              </div>
              <div className="space-y-1.5">
                <Label>Category</Label>
                <CategorySelect
                  value={categoryId}
                  onChange={setCategoryId}
                  allowNone={false}
                  placeholder="Pick a category"
                />
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={subfolders}
                  onChange={(e) => setSubfolders(e.target.checked)}
                />{" "}
                Include subfolders
              </label>
              <div className="flex items-center gap-2">
                <Label htmlFor="rule-prio" className="whitespace-nowrap">
                  Priority
                </Label>
                <Input
                  id="rule-prio"
                  type="number"
                  min={0}
                  className="w-24"
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                />
              </div>
              <div className="sm:col-span-2">
                <Button type="submit" disabled={create.isPending || !categoryId}>
                  {create.isPending ? <LoaderCircle className="animate-spin" /> : <Plus />} Add rule
                </Button>
              </div>
            </form>
          ) : null}
        </CardContent>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Mapping preview</CardTitle>
            <CardDescription>
              Type a folder to see which rule would classify files in it.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Input
              value={previewPath}
              onChange={(e) => setPreviewPath(e.target.value)}
              placeholder="home-living/kitchen/2026"
              aria-label="Preview folder"
              disabled={!finalsRoot}
            />
            {preview.data ? (
              <div className="rounded-md border p-3 text-sm">
                <p>
                  <span className="font-mono">{preview.data.relative_folder_path || "(root)"}</span>{" "}
                  → <span className="font-medium">{preview.data.category_name}</span>
                  {preview.data.fallback ? (
                    <Badge variant="secondary" className="ml-2 text-[10px]">
                      fallback
                    </Badge>
                  ) : null}
                </p>
                {preview.data.rule ? (
                  <p className="text-xs text-muted-foreground">
                    via rule{" "}
                    <span className="font-mono">
                      {preview.data.rule.relative_folder_path || "(root)"}
                    </span>
                    {preview.data.candidates.length > 1
                      ? ` · ${preview.data.candidates.length} rules matched, most specific wins`
                      : ""}
                  </p>
                ) : null}
              </div>
            ) : preview.isError ? (
              <p className="text-xs text-destructive">{preview.error.message}</p>
            ) : null}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Folders on the NAS</CardTitle>
            <CardDescription>
              Folders that currently hold indexed finals and how they map.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {report.isPending ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : report.isError ? (
              <p className="text-sm text-destructive">{report.error.message}</p>
            ) : report.data.length === 0 ? (
              <p className="text-sm text-muted-foreground">No finals indexed yet.</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {report.data.map((entry) => (
                  <li
                    key={`${entry.media_root_id}:${entry.folder}`}
                    className="flex flex-wrap items-center gap-2"
                  >
                    <span className="font-mono">{entry.folder || "(root)"}</span>
                    <span className="text-xs text-muted-foreground">
                      {entry.file_count} file{entry.file_count === 1 ? "" : "s"}
                    </span>
                    {entry.mapped ? (
                      <Badge variant="outline" className="text-[10px]">
                        {entry.rule!.category_name}
                      </Badge>
                    ) : (
                      <Badge
                        variant="outline"
                        className="border-warning/60 text-[10px] text-warning"
                      >
                        unmapped → Uncategorized
                      </Badge>
                    )}
                    {admin && !entry.mapped ? (
                      <Button variant="ghost" size="sm" onClick={() => setPath(entry.folder)}>
                        Map
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            {unmapped.length > 0 ? (
              <p className="mt-2 text-xs text-muted-foreground">
                {unmapped.length} unmapped folder{unmapped.length === 1 ? "" : "s"}.
              </p>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
