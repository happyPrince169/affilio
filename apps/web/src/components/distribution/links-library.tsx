"use client";

import { PLATFORMS, type AffiliateLink, type Platform } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ExternalLink, LoaderCircle, Plus, TriangleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { DistributionTabs } from "@/components/distribution/distribution-tabs";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { createLink, linkQueries, updateLink } from "@/lib/api/content";

export function LinkFormDialog({ trigger, link }: { trigger: ReactNode; link?: AffiliateLink }) {
  const [open, setOpen] = useState(false);
  const [platform, setPlatform] = useState<Platform>(link?.platform ?? "shopee");
  const [label, setLabel] = useState(link?.label ?? "");
  const [url, setUrl] = useState(link?.url ?? "");
  const [notes, setNotes] = useState(link?.notes ?? "");
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () =>
      link
        ? updateLink(link.id, { label: label.trim(), url: url.trim(), notes: notes.trim() || null })
        : createLink({
            platform,
            label: label.trim(),
            url: url.trim(),
            notes: notes.trim() || null,
          }),
    onSuccess: () => {
      toast.success(link ? "Link updated" : "Link added");
      void queryClient.invalidateQueries({ queryKey: linkQueries.all() });
      setOpen(false);
    },
    onError: (error) => toast.error("Could not save link", { description: error.message }),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{link ? "Edit link" : "Add affiliate link"}</DialogTitle>
          <DialogDescription>
            Only http(s) URLs are accepted. Links are reused across publications and frozen into the
            approved snapshot exactly as stored.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          {!link ? (
            <div className="space-y-1.5">
              <Label>Platform</Label>
              <Select value={platform} onValueChange={(v) => setPlatform(v as Platform)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PLATFORMS.map((p) => (
                    <SelectItem key={p} value={p}>
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor="link-label">Label</Label>
            <Input id="link-label" value={label} onChange={(e) => setLabel(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="link-url">URL</Label>
            <Input
              id="link-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://shopee.vn/…"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="link-notes">Notes</Label>
            <Textarea
              id="link-notes"
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending || !label.trim() || !url.trim()}
          >
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            {link ? "Save" : "Add"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function LinksLibrary() {
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const queryClient = useQueryClient();
  const links = useQuery(
    linkQueries.list({ q: q || undefined, status: showArchived ? "" : "active" }),
  );
  const archive = useMutation({
    mutationFn: (link: AffiliateLink) =>
      updateLink(link.id, { status: link.status === "active" ? "archived" : "active" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: linkQueries.all() }),
    onError: (error) => toast.error("Could not update link", { description: error.message }),
  });
  return (
    <div className="space-y-6">
      <PageHeader
        title="Affiliate links"
        description="Reusable library of validated affiliate URLs. Attach one or more to each publication; the approved snapshot keeps the exact URLs and order."
        actions={
          can("edit") ? (
            <LinkFormDialog
              trigger={
                <Button>
                  <Plus /> Add link
                </Button>
              }
            />
          ) : null
        }
      />
      <DistributionTabs />
      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search links…"
          className="max-w-sm"
          aria-label="Search links"
        />
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
          />
          Include archived
        </label>
      </div>
      {links.isPending ? (
        <Skeleton className="h-48 rounded-xl" />
      ) : links.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load links</AlertTitle>
          <AlertDescription>{links.error.message}</AlertDescription>
        </Alert>
      ) : links.data.length === 0 ? (
        <div className="rounded-xl border border-dashed px-6 py-16 text-center text-sm text-muted-foreground">
          No links yet.
        </div>
      ) : (
        <ul className="divide-y rounded-xl border">
          {links.data.map((link) => (
            <li key={link.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
              <Badge variant="outline">{link.platform}</Badge>
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">
                  {link.label}
                  {link.status === "archived" ? (
                    <span className="ml-2 text-xs text-muted-foreground">archived</span>
                  ) : null}
                </p>
                <a
                  href={link.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex max-w-full items-center gap-1 truncate text-xs text-muted-foreground hover:underline"
                >
                  {link.url} <ExternalLink className="size-3 shrink-0" />
                </a>
                {link.product_name ? (
                  <p className="text-xs text-muted-foreground">Product: {link.product_name}</p>
                ) : null}
              </div>
              {can("edit") ? (
                <div className="flex items-center gap-1">
                  <LinkFormDialog
                    link={link}
                    trigger={
                      <Button variant="ghost" size="sm">
                        Edit
                      </Button>
                    }
                  />
                  <Button variant="ghost" size="sm" onClick={() => archive.mutate(link)}>
                    <Archive /> {link.status === "active" ? "Archive" : "Restore"}
                  </Button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
