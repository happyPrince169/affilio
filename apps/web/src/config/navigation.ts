import {
  Bot,
  CalendarDays,
  ChartColumn,
  FolderOpen,
  LayoutDashboard,
  type LucideIcon,
  Package,
  Settings,
  Users,
} from "lucide-react";

export type MilestoneId = "M0" | "M1" | "M2" | "M3" | "M4" | "M5";

export interface NavItem {
  title: string;
  href: string;
  icon: LucideIcon;
  description: string;
  /** Milestone in which this area becomes functional. `M0` means available now. */
  milestone: MilestoneId;
}

export const mainNavigation: NavItem[] = [
  {
    title: "Dashboard",
    href: "/",
    icon: LayoutDashboard,
    description: "System overview and milestone progress.",
    milestone: "M0",
  },
  {
    title: "Content Library",
    href: "/content",
    icon: FolderOpen,
    description: "Centralized media library synced from the Synology NAS.",
    milestone: "M1",
  },
  {
    title: "Products",
    href: "/products",
    icon: Package,
    description: "Affiliate product catalog and research workspace.",
    milestone: "M2",
  },
  {
    title: "Publishing Calendar",
    href: "/calendar",
    icon: CalendarDays,
    description: "Content planning, scheduling, and approval workflow.",
    milestone: "M3",
  },
  {
    title: "Analytics",
    href: "/analytics",
    icon: ChartColumn,
    description: "Affiliate performance, revenue, and commission insights.",
    milestone: "M4",
  },
  {
    title: "AI Assistant",
    href: "/assistant",
    icon: Bot,
    description: "Claude-powered content planning and optimization.",
    milestone: "M5",
  },
  {
    title: "Accounts",
    href: "/accounts",
    icon: Users,
    description: "Connected social and marketplace accounts.",
    milestone: "M3",
  },
  {
    title: "Settings",
    href: "/settings",
    icon: Settings,
    description: "Organization, team, and workspace preferences.",
    milestone: "M3",
  },
];

export function findNavItem(pathname: string): NavItem | undefined {
  return mainNavigation.find((item) =>
    item.href === "/" ? pathname === "/" : pathname.startsWith(item.href),
  );
}
