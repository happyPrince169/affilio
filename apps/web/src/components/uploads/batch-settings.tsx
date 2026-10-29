"use client";

import type { Member, UploadOptions } from "@affilio/shared-types";

import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { BatchDefaults } from "@/lib/uploads/queue";

interface BatchSettingsProps {
  options: UploadOptions;
  defaults: BatchDefaults;
  onChange: (patch: Partial<BatchDefaults>) => void;
  concurrency: number;
  onConcurrency: (n: number) => void;
  /** Members for the admin on-behalf selector; undefined for editors. */
  members?: Member[];
  locked?: boolean;
}

const NONE = "__none__";

export function BatchSettings({
  options,
  defaults,
  onChange,
  concurrency,
  onConcurrency,
  members,
  locked,
}: BatchSettingsProps) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div className="space-y-1.5">
        <Label htmlFor="batch-owner">Production owner</Label>
        <Select
          value={defaults.ownerId ?? ""}
          onValueChange={(v) => onChange({ ownerId: v })}
          disabled={locked || options.owners.length === 0}
        >
          <SelectTrigger id="batch-owner" className="w-full">
            <SelectValue
              placeholder={options.owners.length ? "Choose owner" : "No owner assigned"}
            />
          </SelectTrigger>
          <SelectContent>
            {options.owners.map((o) => (
              <SelectItem key={o.id} value={o.id}>
                {o.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="batch-product">Product category</Label>
        <Select
          value={defaults.productCategoryId ?? ""}
          onValueChange={(v) => onChange({ productCategoryId: v })}
          disabled={locked}
        >
          <SelectTrigger id="batch-product" className="w-full">
            <SelectValue placeholder="Choose category" />
          </SelectTrigger>
          <SelectContent>
            {options.product_categories.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="batch-type">Content type</Label>
        <Select
          value={defaults.contentTypeId ?? ""}
          onValueChange={(v) => onChange({ contentTypeId: v })}
          disabled={locked}
        >
          <SelectTrigger id="batch-type" className="w-full">
            <SelectValue placeholder="Choose type" />
          </SelectTrigger>
          <SelectContent>
            {options.content_types.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {options.can_submit_for_others && members ? (
        <div className="space-y-1.5">
          <Label htmlFor="batch-for">Submitted for (editor)</Label>
          <Select
            value={defaults.submittedForUserId ?? NONE}
            onValueChange={(v) => onChange({ submittedForUserId: v === NONE ? null : v })}
          >
            <SelectTrigger id="batch-for" className="w-full">
              <SelectValue placeholder="Myself" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>Myself</SelectItem>
              {members.map((m) => (
                <SelectItem key={m.user_id} value={m.user_id}>
                  {m.display_name ?? m.email}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : (
        <div className="space-y-1.5">
          <Label htmlFor="batch-concurrency">Parallel uploads</Label>
          <Select value={String(concurrency)} onValueChange={(v) => onConcurrency(Number(v))}>
            <SelectTrigger id="batch-concurrency" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[1, 2, 3, 4].map((n) => (
                <SelectItem key={n} value={String(n)}>
                  {n} at a time
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}
