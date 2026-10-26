import type { ReactNode } from "react";

import { AppSidebar } from "@/components/layout/app-sidebar";
import { TopNav } from "@/components/layout/top-nav";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-svh">
      <AppSidebar />
      <div className="flex min-w-0 flex-1 flex-col lg:pl-64">
        <TopNav />
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          {children}
        </main>
        <footer className="border-t px-4 py-4 text-xs text-muted-foreground sm:px-6 lg:px-8">
          AFFILIO · Create Once. Distribute Everywhere. Optimize Everything.
        </footer>
      </div>
    </div>
  );
}
