"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { UserMenu } from "@/components/auth/user-menu";
import { ApiStatusIndicator } from "@/components/layout/api-status-indicator";
import { MobileNav } from "@/components/layout/mobile-nav";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Separator } from "@/components/ui/separator";
import { findNavItem } from "@/config/navigation";

/** Reads the pathname at runtime; kept behind Suspense so dynamic routes can still prerender. */
function CurrentSection() {
  const pathname = usePathname();
  const current = findNavItem(pathname);
  return <span className="truncate font-medium">{current?.title ?? "Not found"}</span>;
}

export function TopNav() {
  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur sm:px-6">
      <MobileNav />

      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-sm">
        <Link href="/" className="hidden text-muted-foreground hover:text-foreground sm:inline">
          Workspace
        </Link>
        <ChevronRight className="hidden size-4 text-muted-foreground/60 sm:inline" />
        <Suspense fallback={<span className="truncate font-medium">&nbsp;</span>}>
          <CurrentSection />
        </Suspense>
      </nav>

      <div className="ml-auto flex items-center gap-1.5">
        <ApiStatusIndicator />
        <Separator orientation="vertical" className="mx-1 hidden h-6! sm:block" />
        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}
