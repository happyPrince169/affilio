import type { Metadata } from "next";
import { Suspense } from "react";

import { ConnectionPagesRoute } from "@/components/accounts/connection-pages";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Connect Facebook Pages" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <ConnectionPagesRoute />
    </Suspense>
  );
}
