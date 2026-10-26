import type { Metadata } from "next";

import { PlaceholderPage } from "@/components/placeholder-page";

export const metadata: Metadata = { title: "Accounts" };

export default function AccountsPage() {
  return (
    <PlaceholderPage
      title="Accounts"
      description="Social and marketplace accounts owned by this organization."
      milestone="M3"
      milestoneTitle="Publishing Workflow"
      plannedCapabilities={[
        "List social accounts stored in the database (the seed creates one placeholder Shopee account).",
        "Connect accounts through official OAuth flows only, when the platform provides one.",
        "Show connection status honestly: pending, connected, disconnected, disabled.",
        "Encrypted credential storage with rotation and revocation.",
      ]}
      dependencies={[
        "M0 Foundation accepted",
        "Authentication and organization-scoped authorization",
        "Platform developer app approval where required",
      ]}
    />
  );
}
