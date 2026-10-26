import Link from "next/link";
import { Suspense } from "react";

import { BrandLogo } from "@/components/brand/brand-logo";
import { SidebarNav } from "@/components/layout/sidebar-nav";
import { Badge } from "@/components/ui/badge";
import { siteConfig } from "@/config/site";

export function AppSidebar() {
  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground lg:flex">
      <div className="flex h-16 items-center border-b px-5">
        <Link href="/" className="rounded-md outline-none focus-visible:ring-2">
          <BrandLogo />
        </Link>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-4">
        <p className="mb-2 px-3 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">
          Workspace
        </p>
        <Suspense fallback={null}>
          <SidebarNav />
        </Suspense>
      </div>

      <div className="border-t border-sidebar-border p-4">
        <div className="flex items-center justify-between rounded-lg border bg-card px-3 py-2.5">
          <div className="flex flex-col">
            <span className="text-xs font-medium">Milestone {siteConfig.currentMilestone}</span>
            <span className="text-[11px] text-muted-foreground">Distribution Core</span>
          </div>
          <Badge variant="outline" className="font-mono text-[10px]">
            v{siteConfig.version}
          </Badge>
        </div>
      </div>
    </aside>
  );
}
