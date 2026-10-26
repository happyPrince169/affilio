"use client";

import { CAMPAIGN_STATUSES, type CampaignDetail, type CampaignStatus } from "@affilio/shared-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { CAMPAIGN_LABEL } from "@/components/distribution/badges";
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
import { createCampaign, publishingQueries, updateCampaign } from "@/lib/api/publishing";

const orNull = (v: string) => (v.trim() ? v.trim() : null);

export function CampaignFormDialog({
  trigger,
  campaign,
}: {
  trigger: ReactNode;
  campaign?: CampaignDetail;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(campaign?.name ?? "");
  const [description, setDescription] = useState(campaign?.description ?? "");
  const [status, setStatus] = useState<CampaignStatus>(campaign?.status ?? "draft");
  const [startsOn, setStartsOn] = useState(campaign?.starts_on ?? "");
  const [endsOn, setEndsOn] = useState(campaign?.ends_on ?? "");
  const [notes, setNotes] = useState(campaign?.notes ?? "");
  const router = useRouter();
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => {
      const base = {
        name: name.trim(),
        description: orNull(description),
        starts_on: orNull(startsOn),
        ends_on: orNull(endsOn),
        notes: orNull(notes),
      };
      return campaign ? updateCampaign(campaign.id, { ...base, status }) : createCampaign(base);
    },
    onSuccess: (saved) => {
      toast.success(campaign ? "Campaign updated" : "Campaign created");
      void queryClient.invalidateQueries({ queryKey: publishingQueries.all() });
      setOpen(false);
      if (!campaign) router.push(`/distribution/campaigns/${saved.id}`);
    },
    onError: (error) => toast.error("Could not save campaign", { description: error.message }),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{campaign ? "Edit campaign" : "New campaign"}</DialogTitle>
          <DialogDescription>
            A campaign groups approved final videos and target accounts. Each video × account pair
            becomes its own publication with unique content.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="c-name">Name</Label>
            <Input id="c-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="c-desc">Description</Label>
            <Textarea
              id="c-desc"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="c-start">Starts on</Label>
              <Input
                id="c-start"
                type="date"
                value={startsOn}
                onChange={(e) => setStartsOn(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="c-end">Ends on</Label>
              <Input
                id="c-end"
                type="date"
                value={endsOn}
                onChange={(e) => setEndsOn(e.target.value)}
              />
            </div>
          </div>
          {campaign ? (
            <div className="space-y-1.5">
              <Label>Status</Label>
              <Select value={status} onValueChange={(v) => setStatus(v as CampaignStatus)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CAMPAIGN_STATUSES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {CAMPAIGN_LABEL[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor="c-notes">Notes</Label>
            <Textarea
              id="c-notes"
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
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending || !name.trim()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            {campaign ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
