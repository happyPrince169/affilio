import type { Metadata } from "next";
import { Suspense } from "react";

import { CalendarView } from "@/components/distribution/calendar-view";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Calendar" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <CalendarView />
    </Suspense>
  );
}
