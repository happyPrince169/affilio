import type { Metadata } from "next";

import { AccountsRegistry } from "@/components/accounts/accounts-registry";

export const metadata: Metadata = { title: "Accounts" };

export default function AccountsPage() {
  return <AccountsRegistry />;
}
