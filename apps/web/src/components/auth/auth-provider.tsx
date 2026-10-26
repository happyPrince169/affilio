"use client";

import type { CurrentUser } from "@affilio/shared-types";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { authQueries } from "@/lib/api/auth";
import { ApiError, UNAUTHENTICATED_EVENT } from "@/lib/api/client";

interface AuthContextValue {
  user: CurrentUser;
  /** IANA zone of the active organization, used to display schedules. */
  timezone: string;
  can: (permission: keyof CurrentUser["permissions"]) => boolean;
  refresh: () => Promise<unknown>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <RequireAuth>");
  return value;
}

/** Same as useAuth but tolerant of being rendered outside the guard (returns null). */
export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext);
}

/** Login URL that returns the user to where they were. Read at redirect time (client only). */
function loginHref(): string {
  const here =
    typeof window === "undefined" ? "/" : window.location.pathname + window.location.search;
  const next = here && here !== "/" ? `?next=${encodeURIComponent(here)}` : "";
  return `/login${next}`;
}

/**
 * Route guard: resolves the session via `GET /auth/me`. Without a valid session the user is
 * sent to /login; nothing behind the guard renders without an identity (fail closed).
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const me = useQuery(authQueries.me());

  const unauthenticated = me.isError && me.error instanceof ApiError && me.error.status === 401;

  useEffect(() => {
    if (unauthenticated) router.replace(loginHref());
  }, [unauthenticated, router]);

  useEffect(() => {
    const onUnauthenticated = () => {
      queryClient.setQueryData(authQueries.me().queryKey, undefined);
      void queryClient.invalidateQueries({ queryKey: authQueries.me().queryKey });
      router.replace(loginHref());
    };
    window.addEventListener(UNAUTHENTICATED_EVENT, onUnauthenticated);
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onUnauthenticated);
  }, [queryClient, router]);

  const value = useMemo<AuthContextValue | null>(
    () =>
      me.data
        ? {
            user: me.data,
            timezone: me.data.organization.timezone,
            can: (permission) => me.data.permissions[permission] === true,
            refresh: () => me.refetch(),
          }
        : null,
    [me],
  );

  if (value) return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;

  if (me.isError && !unauthenticated) {
    return (
      <div className="mx-auto max-w-md space-y-3 px-4 py-24 text-center">
        <h1 className="text-lg font-semibold">Cannot reach AFFILIO</h1>
        <p className="text-sm text-muted-foreground">{me.error.message}</p>
        <button
          type="button"
          onClick={() => void me.refetch()}
          className="rounded-md border px-3 py-1.5 text-sm hover:bg-accent"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="flex min-h-svh">
      <div className="hidden w-64 border-r p-4 lg:block">
        <Skeleton className="h-8 w-32" />
      </div>
      <div className="flex-1 space-y-4 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 rounded-xl" />
      </div>
    </div>
  );
}
