import type { MilestoneId } from "@/config/navigation";

export type MilestoneStatus = "done" | "in-progress" | "planned";

export interface Milestone {
  id: MilestoneId;
  title: string;
  summary: string;
  status: MilestoneStatus;
}

/**
 * Static roadmap shown on the dashboard. Mirrors docs/milestones/*.md.
 * Update when a milestone is accepted.
 */
export const milestones: Milestone[] = [
  {
    id: "M0",
    title: "Foundation",
    summary: "Monorepo, API skeleton, database migrations, dashboard shell.",
    status: "done",
  },
  {
    id: "M1",
    title: "Media Library",
    summary: "NAS-backed media indexing, metadata, thumbnails, and browsing.",
    status: "done",
  },
  {
    id: "M2",
    title: "Product Catalog",
    summary: "Product catalog, research lifecycle, and product↔video mapping.",
    status: "in-progress",
  },
  {
    id: "M3",
    title: "Publishing Workflow",
    summary: "Content calendar, approvals, and assisted Shopee Video publishing.",
    status: "planned",
  },
  {
    id: "M4",
    title: "Analytics",
    summary: "Affiliate performance tracking and commission reporting.",
    status: "planned",
  },
  {
    id: "M5",
    title: "Claude Agent",
    summary: "Tool-based AI assistant with human approval gates.",
    status: "planned",
  },
];
