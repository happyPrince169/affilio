import { cn } from "cn";

import { siteConfig } from "@/config/site";

interface BrandLogoProps {
  className?: string;
  showWordmark?: boolean;
}

export function BrandLogo({ className, showWordmark = true }: BrandLogoProps) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <span
        aria-hidden
        className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand text-sm font-bold tracking-tight text-brand-foreground shadow-sm"
      >
        A
      </span>
      {showWordmark ? (
        <span className="flex flex-col leading-none">
          <span className="text-sm font-semibold tracking-[0.18em]">{siteConfig.name}</span>
          <span className="mt-0.5 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
            Affiliate Content OS
          </span>
        </span>
      ) : null}
    </div>
  );
}
