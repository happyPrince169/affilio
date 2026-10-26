import type { Metadata } from "next";

import { PlaceholderPage } from "@/components/placeholder-page";

export const metadata: Metadata = { title: "AI Assistant" };

export default function AssistantPage() {
  return (
    <PlaceholderPage
      title="AI Assistant"
      description="Claude-powered assistant for product research, content planning, and optimization."
      milestone="M5"
      milestoneTitle="Claude Agent"
      plannedCapabilities={[
        "Tool-based agent with structured JSON outputs, no free-form side effects.",
        "Caption and hook generation grounded in product and performance data.",
        "Content plan suggestions for the publishing calendar.",
        "Human approval gate for every action that publishes or modifies records.",
      ]}
      dependencies={[
        "M0 Foundation accepted",
        "M2 Product Catalog and M4 Analytics data to ground the agent",
        "Anthropic API key configured via environment (never committed)",
      ]}
    />
  );
}
