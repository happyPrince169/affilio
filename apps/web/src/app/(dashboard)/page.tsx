import { Badge } from "@/components/ui/badge";
import { GettingStartedCard } from "@/components/dashboard/getting-started-card";
import { MilestoneRoadmap } from "@/components/dashboard/milestone-roadmap";
import { OverviewTiles } from "@/components/dashboard/overview-tiles";
import { SystemStatusCard } from "@/components/dashboard/system-status-card";
import { PageHeader } from "@/components/layout/page-header";
import { siteConfig } from "@/config/site";

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description={siteConfig.tagline}
        actions={
          <Badge variant="secondary" className="font-mono">
            {siteConfig.currentMilestone} · Distribution Core
          </Badge>
        }
      />

      <OverviewTiles />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <SystemStatusCard />
        </div>
        <MilestoneRoadmap />
      </div>

      <GettingStartedCard />
    </div>
  );
}
