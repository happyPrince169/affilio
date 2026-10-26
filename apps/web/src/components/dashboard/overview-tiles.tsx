import { AccountsStatsTile } from "@/components/dashboard/accounts-stats-tile";
import { DistributionStatsTile } from "@/components/dashboard/distribution-stats-tile";
import { MediaStatsTile } from "@/components/dashboard/media-stats-tile";
import { ProductStatsTile } from "@/components/dashboard/product-stats-tile";

/** Overview tiles. Every number is backed by real data; nothing is fabricated. */
export function OverviewTiles() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <MediaStatsTile />
      <ProductStatsTile />
      <DistributionStatsTile />
      <AccountsStatsTile />
    </div>
  );
}
