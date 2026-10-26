import Link from "next/link";

import { AppShell } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <AppShell>
      <div className="mx-auto flex max-w-md flex-col items-center gap-4 py-20 text-center">
        <p className="font-mono text-sm font-semibold text-brand">404</p>
        <h1 className="text-2xl font-semibold tracking-tight">Page not found</h1>
        <p className="text-sm text-muted-foreground">
          The page you requested does not exist in this workspace.
        </p>
        <Button asChild>
          <Link href="/">Back to dashboard</Link>
        </Button>
      </div>
    </AppShell>
  );
}
