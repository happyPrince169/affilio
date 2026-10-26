import type { Metadata } from "next";

import { PlaceholderPage } from "@/components/placeholder-page";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <PlaceholderPage
      title="Settings"
      description="Organization profile, team membership, and workspace preferences."
      milestone="M3"
      milestoneTitle="Publishing Workflow"
      plannedCapabilities={[
        "Organization profile and slug management.",
        "Team invitations with role-based access (owner, admin, editor, viewer).",
        "Workspace preferences such as timezone and default account.",
        "API key management for integrations, stored encrypted.",
      ]}
      dependencies={["M0 Foundation accepted", "Authentication design finalized"]}
    />
  );
}
