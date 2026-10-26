import type { Metadata } from "next";
import { Suspense } from "react";

import { LinksLibrary } from "@/components/distribution/links-library";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Affiliate links" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <LinksLibrary />
    </Suspense>
  );
}
