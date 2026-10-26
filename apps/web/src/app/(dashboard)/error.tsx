"use client";

import { TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surface to the browser console; a structured error sink is a later milestone.
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto max-w-xl space-y-4 py-12">
      <Alert variant="destructive">
        <TriangleAlert />
        <AlertTitle>Something went wrong rendering this page</AlertTitle>
        <AlertDescription>
          <p>{error.message || "An unexpected error occurred."}</p>
          {error.digest ? (
            <p className="mt-1 font-mono text-xs opacity-80">Digest: {error.digest}</p>
          ) : null}
        </AlertDescription>
      </Alert>
      <div className="flex gap-2">
        <Button onClick={reset}>Try again</Button>
        <Button variant="outline" asChild>
          <Link href="/">Go to dashboard</Link>
        </Button>
      </div>
    </div>
  );
}
