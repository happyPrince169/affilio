import {
  Bot,
  ChartColumn,
  Clapperboard,
  FolderOpen,
  LayoutDashboard,
  type LucideIcon,
  Package,
  Send,
  Settings,
  ShoppingCart,
  Users,
} from "lucide-react";

export type MilestoneId = "M0" | "M1" | "M2" | "M3" | "M4" | "M5";

export interface NavItem {
  title: string;
  href: string;
  icon: LucideIcon;
  description: string;
  /** Milestone in which this area becomes functional. */
  milestone: MilestoneId;
  /** True once the area is implemented; planned areas render a placeholder. */
  available: boolean;
}

export const mainNavigation: NavItem[] = [
  {
    title: "Dashboard",
    href: "/",
    icon: LayoutDashboard,
    description: "System overview and milestone progress.",
    milestone: "M0",
    available: true,
  },
  {
    title: "Content Library",
    href: "/content",
    icon: FolderOpen,
    description: "Centralized media library indexed from the Synology NAS.",
    milestone: "M1",
    available: true,
  },
  {
    title: "Products",
    href: "/products",
    icon: Package,
    description: "Affiliate product catalog and research workspace.",
    milestone: "M2",
    available: true,
  },
  {
    title: "Purchases",
    href: "/purchases",
    icon: ShoppingCart,
    description: "Purchase requests, orders, deliveries and received units.",
    milestone: "M2",
    available: true,
  },
  {
    title: "Production",
    href: "/production",
    icon: Clapperboard,
    description: "Filming and editing jobs with optional product and video associations.",
    milestone: "M2",
    available: true,
  },
  {
    title: "Distribution",
    href: "/distribution",
    icon: Send,
    description: "Campaigns, composer, calendar, publishing queue and assisted publishing.",
    milestone: "M3",
    available: true,
  },
  {
    title: "Analytics",
    href: "/analytics",
    icon: ChartColumn,
    description: "Affiliate performance, revenue, and commission insights.",
    milestone: "M4",
    available: false,
  },
  {
    title: "AI Assistant",
    href: "/assistant",
    icon: Bot,
    description: "Claude-powered content planning and optimization.",
    milestone: "M5",
    available: false,
  },
  {
    title: "Accounts",
    href: "/accounts",
    icon: Users,
    description: "Registered social accounts. Manual records stay unverified until OAuth (M3B).",
    milestone: "M3",
    available: true,
  },
  {
    title: "Settings",
    href: "/settings",
    icon: Settings,
    description: "Organization timezone, team members and roles, your password.",
    milestone: "M3",
    available: true,
  },
];

export function findNavItem(pathname: string): NavItem | undefined {
  return mainNavigation.find((item) =>
    item.href === "/" ? pathname === "/" : pathname.startsWith(item.href),
  );
}
