import { CalendarDays, Film, Package, Users } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import type { MilestoneId } from "@/config/navigation";

interface Tile {
  label: string;
  icon: typeof Film;
  milestone: MilestoneId;
  note: string;
}

const tiles: Tile[] = [
  {
    label: "Connected accounts",
    icon: Users,
    milestone: "M3",
    note: "No platform is connected. Official integrations only.",
  },
  {
    label: "Media assets",
    icon: Film,
    milestone: "M1",
    note: "Synology NAS sync is not configured yet.",
  },
  {
    label: "Products tracked",
    icon: Package,
    milestone: "M2",
    note: "Product catalog arrives with M2.",
  },
  {
    label: "Scheduled posts",
    icon: CalendarDays,
    milestone: "M3",
    note: "Publishing workflow arrives with M3.",
  },
];

/**
 * Overview tiles intentionally show no numbers: there is no real data in M0,
 * and fabricated metrics are explicitly out of bounds.
 */
export function OverviewTiles() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {tiles.map((tile) => {
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
