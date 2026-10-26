"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LogOut, Settings, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { useOptionalAuth } from "@/components/auth/auth-provider";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { logout } from "@/lib/api/auth";

function initials(email: string, displayName: string | null): string {
  const source = displayName?.trim() || email;
  const parts = source.split(/[\s@._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

export function UserMenu() {
  const auth = useOptionalAuth();
  const router = useRouter();
  const queryClient = useQueryClient();
  const signOut = useMutation({
    mutationFn: logout,
    onSettled: () => {
      queryClient.clear();
      router.replace("/login");
    },
    onError: (error) => toast.error("Sign out failed", { description: error.message }),
  });

  // Rendered outside the auth guard (e.g. the global not-found page): offer sign-in instead.
  if (!auth) {
    return (
      <Link href="/login" className="ml-1 text-sm text-muted-foreground hover:text-foreground">
        Sign in
      </Link>
    );
  }
  const { user } = auth;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="ml-1 rounded-full outline-none focus-visible:ring-2"
          aria-label="Account menu"
        >
          <Avatar className="size-8">
            <AvatarFallback className="bg-accent text-xs font-semibold text-accent-foreground">
              {initials(user.email, user.display_name)}
            </AvatarFallback>
          </Avatar>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="space-y-1 font-normal">
          <p className="truncate text-sm font-medium">{user.display_name ?? user.email}</p>
          <p className="truncate text-xs text-muted-foreground">{user.email}</p>
          <div className="flex items-center gap-1.5 pt-1">
            <Badge variant="secondary" className="gap-1 text-[10px] capitalize">
              <ShieldCheck className="size-3" /> {user.organization.role}
            </Badge>
            <span className="truncate text-[11px] text-muted-foreground">
              {user.organization.organization_name}
            </span>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/settings">
            <Settings /> Settings
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => signOut.mutate()} disabled={signOut.isPending}>
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
