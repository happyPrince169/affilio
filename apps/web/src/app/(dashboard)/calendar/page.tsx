import type { Metadata } from "next";

import { PlaceholderPage } from "@/components/placeholder-page";

export const metadata: Metadata = { title: "Publishing Calendar" };

export default function PublishingCalendarPage() {
  return (
    <PlaceholderPage
      title="Publishing Calendar"
      description="Plan, schedule, approve, and track content across connected accounts."
      milestone="M3"
      milestoneTitle="Publishing Workflow"
      plannedCapabilities={[
        "Platform-agnostic content items with per-platform variants.",
        "Draft → review → approved → scheduled → published state machine.",
        "Assisted Shopee Video publishing using only official, confirmed APIs.",
        "Manual publish checklist fallback when no official API is available.",
      ]}
      dependencies={[
        "M0 Foundation accepted",
        "M1 Media Library and M2 Product Catalog",
        "Confirmation of Shopee Video API availability and permissions",
      ]}
    />
  );
}
