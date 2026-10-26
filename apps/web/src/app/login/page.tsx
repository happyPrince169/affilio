import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "@/components/auth/login-form";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <main className="flex min-h-svh items-center justify-center bg-muted/30 px-4 py-10">
      <Suspense fallback={<Skeleton className="h-96 w-full max-w-sm rounded-xl" />}>
        <LoginForm />
      </Suspense>
    </main>
  );
}
