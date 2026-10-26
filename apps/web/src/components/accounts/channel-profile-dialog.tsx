"use client";

import type {
  ChannelCategoryRuleInput,
  ChannelMatchPolicy,
  SocialAccount,
} from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Route, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { CategorySelect } from "@/components/taxonomy/category-select";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { taxonomyQueries } from "@/lib/api/taxonomy";
import { replaceChannelProfile, routingQueries } from "@/lib/api/routing";

const POLICY_LABEL: Record<ChannelMatchPolicy, string> = {
  prefer: "Prefer",
  allow: "Allow",
  block: "Block",
};
const POLICY_CLASS: Record<ChannelMatchPolicy, string> = {
  prefer: "border-success/60 text-success",
  allow: "",
  block: "border-destructive/60 text-destructive",
};

export function ChannelProfileSummary({ accountId }: { accountId: string }) {
  const profile = useQuery(routingQueries.profile(accountId));
  if (!profile.data) return null;
  const p = profile.data;
  return (
    <div className="flex flex-wrap gap-1 text-[11px]">
      {!p.routing_enabled ? <Badge variant="secondary">routing off</Badge> : null}
      {p.primary_category ? (
        <Badge variant="outline">focus: {p.primary_category.name}</Badge>
      ) : null}
      {p.rules.map((r) => (
        <Badge key={r.category.id} variant="outline" className={POLICY_CLASS[r.match_policy]}>
          {POLICY_LABEL[r.match_policy].toLowerCase()} {r.category.name}
        </Badge>
      ))}
      {p.target_audience ? (
        <span className="text-muted-foreground">· {p.target_audience}</span>
      ) : null}
    </div>
  );
}

export function ChannelProfileDialog({
  account,
  trigger,
}: {
  account: SocialAccount;
  trigger: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const profile = useQuery({ ...routingQueries.profile(account.id), enabled: open });
  const categories = useQuery(taxonomyQueries.categories());
  const [loaded, setLoaded] = useState(false);
  const [primary, setPrimary] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [audience, setAudience] = useState("");
  const [language, setLanguage] = useState("");
  const [country, setCountry] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [rules, setRules] = useState<ChannelCategoryRuleInput[]>([]);
  const [newCategory, setNewCategory] = useState<string | null>(null);
  const [newPolicy, setNewPolicy] = useState<ChannelMatchPolicy>("allow");
  if (open && profile.data && !loaded) {
    setPrimary(profile.data.primary_category?.id ?? null);
    setDescription(profile.data.description ?? "");
    setAudience(profile.data.target_audience ?? "");
    setLanguage(profile.data.language ?? "");
    setCountry(profile.data.country ?? "");
    setEnabled(profile.data.routing_enabled);
    setRules(
      profile.data.rules.map((r) => ({
        category_id: r.category.id,
        match_policy: r.match_policy,
        priority: r.priority,
      })),
    );
    setLoaded(true);
  }
  const nameOf = (id: string) => categories.data?.find((c) => c.id === id)?.path ?? id;
  const mutation = useMutation({
    mutationFn: () =>
      replaceChannelProfile(account.id, {
        primary_category_id: primary,
        description: description.trim() || null,
        target_audience: audience.trim() || null,
        language: language.trim() || null,
        country: country.trim() || null,
        routing_enabled: enabled,
        rules,
      }),
    onSuccess: () => {
      toast.success("Channel profile saved");
      void queryClient.invalidateQueries({ queryKey: routingQueries.all() });
      setOpen(false);
    },
    onError: (error) => toast.error("Could not save profile", { description: error.message }),
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setLoaded(false);
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Route className="size-4" /> Channel profile · {account.display_name}
          </DialogTitle>
          <DialogDescription>
            Which content this Page should receive. Rules inherit down the category tree; Block
            always wins over Allow and Prefer. This only affects recommendations, never publishing.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />{" "}
            Routing enabled
          </label>
          <div className="space-y-1.5">
            <Label>Primary focus category (+10 relevance when it matches)</Label>
            <CategorySelect value={primary} onChange={setPrimary} placeholder="Focus category" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="cp-aud">Target audience</Label>
              <Input
                id="cp-aud"
                value={audience}
                onChange={(e) => setAudience(e.target.value)}
                placeholder="Men 25–40, Vietnam"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1.5">
                <Label htmlFor="cp-lang">Language</Label>
                <Input
                  id="cp-lang"
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                  placeholder="vi"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="cp-country">Country</Label>
                <Input
                  id="cp-country"
                  value={country}
                  onChange={(e) => setCountry(e.target.value)}
                  placeholder="VN"
                  maxLength={2}
                />
              </div>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cp-desc">Description</Label>
            <Textarea
              id="cp-desc"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label>Category rules</Label>
            {rules.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No rules: this Page is never recommended, only selectable with a warning.
              </p>
            ) : null}
            <ul className="space-y-1">
              {rules.map((r) => (
                <li
                  key={r.category_id}
                  className="flex items-center gap-2 rounded-md border px-2 py-1 text-sm"
                >
                  <Badge variant="outline" className={POLICY_CLASS[r.match_policy]}>
                    {POLICY_LABEL[r.match_policy]}
                  </Badge>
                  <span className="min-w-0 flex-1 truncate">{nameOf(r.category_id)}</span>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label="Remove rule"
                    onClick={() => setRules(rules.filter((x) => x.category_id !== r.category_id))}
                  >
                    <X />
                  </Button>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={newPolicy}
                onValueChange={(v) => setNewPolicy(v as ChannelMatchPolicy)}
              >
                <SelectTrigger className="w-28" size="sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="prefer">Prefer</SelectItem>
                  <SelectItem value="allow">Allow</SelectItem>
                  <SelectItem value="block">Block</SelectItem>
                </SelectContent>
              </Select>
              <CategorySelect
                value={newCategory}
                onChange={setNewCategory}
                allowNone={false}
                placeholder="Rule category"
                className="w-56"
                size="sm"
                exclude={new Set(rules.map((r) => r.category_id))}
              />
              <Button
                variant="outline"
                size="sm"
                disabled={!newCategory}
                onClick={() => {
                  if (newCategory)
                    setRules([
                      ...rules,
                      { category_id: newCategory, match_policy: newPolicy, priority: 0 },
                    ]);
                  setNewCategory(null);
                }}
              >
                Add rule
              </Button>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending || !loaded}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Save profile
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
