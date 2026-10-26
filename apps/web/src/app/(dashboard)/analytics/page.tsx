import type { Metadata } from "next";

import { PlaceholderPage } from "@/components/placeholder-page";

export const metadata: Metadata = { title: "Analytics" };

export default function AnalyticsPage() {
  return (
    <PlaceholderPage
      title="Analytics"
      description="Affiliate performance, revenue, and commission intelligence."
      milestone="M4"
      milestoneTitle="Analytics"
      plannedCapabilities={[
        "Manual and file-based import of Shopee Affiliate performance reports.",
        "Attribution of clicks, orders, and commissions to published content.",
        "Per-account and per-product performance views.",
        "Export-ready reporting for the operations team.",
      ]}
      dependencies={["M0 Foundation accepted", "M3 Publishing Workflow (content identity)"]}
    />
  );
}
