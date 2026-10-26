"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, LockKeyhole } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { BrandLogo } from "@/components/brand/brand-logo";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { authQueries, login } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

function safeNext(raw: string | null): string {
  // Only same-origin relative paths; never an absolute URL (open-redirect guard).
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export function LoginForm() {
  const router = useRouter();
  const search = useSearchParams();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const mutation = useMutation({
    mutationFn: () => login({ email: email.trim(), password }),
    onSuccess: (me) => {
      queryClient.setQueryData(authQueries.me().queryKey, me);
      router.replace(safeNext(search.get("next")));
    },
  });

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!mutation.isPending) mutation.mutate();
  };

  const errorMessage =
    mutation.error instanceof ApiError && mutation.error.status === 401
      ? "Email or password is incorrect."
      : mutation.error?.message;

  return (
    <Card className="w-full max-w-sm">
      <CardHeader className="space-y-3">
        <BrandLogo />
        <div>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>Use the account an administrator created for you.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {errorMessage ? (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage}</AlertDescription>
            </Alert>
          ) : null}
          <Button
            type="submit"
            className="w-full"
            disabled={mutation.isPending || !email || !password}
          >
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : <LockKeyhole />}
            Sign in
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            Sessions are HttpOnly cookies. No passwords or tokens are stored in the browser.
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
