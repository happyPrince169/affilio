import type { Metadata } from "next";
import { Suspense } from "react";

import { AccountsRegistry } from "@/components/accounts/accounts-registry";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Accounts" };

export default function AccountsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <AccountsRegistry />
    </Suspense>
  );
}
