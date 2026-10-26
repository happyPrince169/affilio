"use client";

import { cn } from "cn";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { mainNavigation, type NavItem } from "@/config/navigation";

interface SidebarNavProps {
  onNavigate?: () => void;
  className?: string;
}

function isActive(pathname: string, item: NavItem) {
  return item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
}

export function SidebarNav({ onNavigate, className }: SidebarNavProps) {
  const pathname = usePathname();

  return (
    <nav aria-label="Primary" className={cn("flex flex-col gap-1", className)}>
      {mainNavigation.map((item) => {
        const active = isActive(pathname, item);
        const Icon = item.icon;
        const planned = !item.available;

        return (
          <Tooltip key={item.href}>
            <TooltipTrigger asChild>
              <Link
                href={item.href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors outline-none",
                  "focus-visible:ring-2 focus-visible:ring-sidebar-ring",
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
                )}
              >
                <Icon
                  className={cn(
                    "size-4 shrink-0",
                    active ? "text-brand" : "text-muted-foreground group-hover:text-foreground",
                  )}
                />
                <span className="flex-1 truncate">{item.title}</span>
                {planned ? (
                  <span className="rounded-md border px-1.5 py-0.5 font-mono text-[10px] leading-none text-muted-foreground/80">
                    {item.milestone}
                  </span>
                ) : null}
              </Link>
            </TooltipTrigger>
            <TooltipContent side="right" className="max-w-56">
              <p>{item.description}</p>
              {planned ? (
                <p className="mt-1 opacity-80">
                  Not implemented yet. Planned for {item.milestone}.
                </p>
              ) : null}
            </TooltipContent>
          </Tooltip>
        );
      })}
    </nav>
  );
}
