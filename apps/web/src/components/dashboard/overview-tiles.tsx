import { CalendarDays, Users } from "lucide-react";

import { MediaStatsTile } from "@/components/dashboard/media-stats-tile";
import { ProductStatsTile } from "@/components/dashboard/product-stats-tile";
import { Card, CardContent } from "@/components/ui/card";
import type { MilestoneId } from "@/config/navigation";

interface Tile {
  label: string;
  icon: typeof Users;
  milestone: MilestoneId;
  note: string;
}

const plannedTiles: Tile[] = [
  {
    label: "Connected accounts",
    icon: Users,
    milestone: "M3",
    note: "No platform is connected. Official integrations only.",
  },
  {
    label: "Scheduled posts",
    icon: CalendarDays,
    milestone: "M3",
    note: "Publishing workflow arrives with M3.",
  },
];

/**
 * Overview tiles. The media tile is live (M1); the others intentionally show no numbers
 * because there is no real data behind them yet.
 */
export function OverviewTiles() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <MediaStatsTile />
      <ProductStatsTile />
      {plannedTiles.map((tile) => {
        const Icon = tile.icon;
        return (
          <Card key={tile.label} className="gap-3 py-5">
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-muted-foreground">{tile.label}</span>
                <Icon className="size-4 text-muted-foreground" />
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-semibold tracking-tight text-muted-foreground/60">
                  —
                </span>
                <span className="rounded-md border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                  {tile.milestone}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">{tile.note}</p>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
