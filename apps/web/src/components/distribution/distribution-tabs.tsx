"use client";

import { cn } from "cn";
import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/distribution/campaigns", label: "Campaigns" },
  { href: "/distribution/compose", label: "Composer" },
  { href: "/distribution/calendar", label: "Calendar" },
  { href: "/distribution/queue", label: "Queue" },
  { href: "/distribution/links", label: "Links" },
] as const;

export function DistributionTabs() {
  const pathname = usePathname();
  return (
    <nav aria-label="Distribution sections" className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <div className="inline-flex min-w-full gap-1 border-b sm:min-w-0">
        {TABS.map((tab) => {
          const active =
            pathname === tab.href ||
            pathname.startsWith(`${tab.href}/`) ||
            (tab.href === "/distribution/campaigns" &&
              pathname.startsWith("/distribution/publications"));
          return (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors",
                active
                  ? "border-brand text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {tab.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
