import { cn } from "cn";
import { CircleCheck, CircleDashed, LoaderCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { milestones, type MilestoneStatus } from "@/config/milestones";

const statusMeta: Record<
  MilestoneStatus,
  { label: string; icon: typeof CircleCheck; className: string }
> = {
  done: { label: "Done", icon: CircleCheck, className: "text-success" },
  "in-progress": { label: "In progress", icon: LoaderCircle, className: "text-brand" },
  planned: { label: "Planned", icon: CircleDashed, className: "text-muted-foreground/60" },
};

export function MilestoneRoadmap() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Delivery roadmap</CardTitle>
        <CardDescription>
          Milestones are delivered one at a time and verified independently.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="relative space-y-4 border-l pl-5">
          {milestones.map((milestone) => {
            const meta = statusMeta[milestone.status];
            const Icon = meta.icon;
            return (
              <li key={milestone.id} className="relative">
                <span
                  className={cn(
                    "absolute top-0.5 -left-[27px] flex size-4 items-center justify-center rounded-full bg-card",
                    meta.className,
                  )}
                >
                  <Icon className="size-4" />
                </span>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs font-semibold">{milestone.id}</span>
                  <span className="text-sm font-medium">{milestone.title}</span>
                  <Badge
                    variant={milestone.status === "in-progress" ? "default" : "outline"}
                    className="ml-auto text-[10px]"
                  >
                    {meta.label}
                  </Badge>
                </div>
                <p className="mt-0.5 text-xs text-muted-foreground">{milestone.summary}</p>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
