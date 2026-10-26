"use client";

import {
  PLATFORMS,
  SOCIAL_ACCOUNT_TYPES,
  type Platform,
  type SocialAccount,
  type SocialAccountType,
} from "@affilio/shared-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

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
import { accountQueries, registerAccount, updateAccount } from "@/lib/api/accounts";

const orNull = (v: string) => (v.trim() ? v.trim() : null);

export function RegisterAccountDialog({
  trigger,
  account,
}: {
  trigger: ReactNode;
  account?: SocialAccount;
}) {
  const [open, setOpen] = useState(false);
  const [platform, setPlatform] = useState<Platform>(account?.platform ?? "facebook");
  const [type, setType] = useState<SocialAccountType>(account?.account_type ?? "page");
  const [name, setName] = useState(account?.display_name ?? "");
  const [handle, setHandle] = useState(account?.handle ?? "");
  const [externalId, setExternalId] = useState(account?.external_account_id ?? "");
  const [profileUrl, setProfileUrl] = useState(account?.profile_url ?? "");
  const [notes, setNotes] = useState(account?.notes ?? "");
  const queryClient = useQueryClient();
  const editing = Boolean(account);

  const mutation = useMutation({
    mutationFn: () =>
      account
        ? updateAccount(account.id, {
            display_name: name.trim(),
            handle: orNull(handle),
            external_account_id: orNull(externalId),
            profile_url: orNull(profileUrl),
            notes: orNull(notes),
          })
        : registerAccount({
            platform,
            account_type: type,
            display_name: name.trim(),
            handle: orNull(handle),
            external_account_id: orNull(externalId),
            profile_url: orNull(profileUrl),
            notes: orNull(notes),
          }),
    onSuccess: () => {
      toast.success(editing ? "Account updated" : "Account registered (unverified)");
      void queryClient.invalidateQueries({ queryKey: accountQueries.all() });
      setOpen(false);
    },
    onError: (error) =>
      toast.error(editing ? "Could not update account" : "Could not register account", {
        description: error.message,
      }),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit account" : "Register a Page manually"}</DialogTitle>
          <DialogDescription>
            This creates an unverified, disconnected record so publications can target it. No token
            is requested or stored. Connecting via Meta OAuth arrives in M3B.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          {!editing ? (
            <div className="grid grid-cols-2 gap-3">
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
              <div className="space-y-1.5">
                <Label>Type</Label>
                <Select value={type} onValueChange={(v) => setType(v as SocialAccountType)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SOCIAL_ACCOUNT_TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {t}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor="acc-name">Display name</Label>
            <Input id="acc-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="acc-handle">Handle</Label>
              <Input
                id="acc-handle"
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
                placeholder="PageUsername"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="acc-ext">Platform id (optional)</Label>
              <Input
                id="acc-ext"
                value={externalId}
                onChange={(e) => setExternalId(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="acc-url">Profile URL (optional)</Label>
            <Input
              id="acc-url"
              value={profileUrl}
              onChange={(e) => setProfileUrl(e.target.value)}
              placeholder="https://www.facebook.com/PageUsername"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="acc-notes">Notes</Label>
            <Textarea
              id="acc-notes"
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending || !name.trim()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            {editing ? "Save" : "Register"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
