"use client";

import { MEMBERSHIP_ROLES, type MembershipRole } from "@affilio/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, TriangleAlert, UserPlus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CategoriesManager } from "@/components/taxonomy/categories-manager";
import { FolderRulesManager } from "@/components/taxonomy/folder-rules-manager";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  authQueries,
  changePassword,
  createMember,
  revokeMemberSessions,
  updateMemberRole,
  updateOrganization,
} from "@/lib/api/auth";
import { formatDateTime } from "@/lib/format";

const ASSIGNABLE_ROLES = MEMBERSHIP_ROLES.filter((r) => r !== "owner") as Exclude<
  MembershipRole,
  "owner"
>[];
const COMMON_ZONES = [
  "Asia/Ho_Chi_Minh",
  "Asia/Bangkok",
  "Asia/Singapore",
  "Asia/Manila",
  "Asia/Jakarta",
  "Asia/Tokyo",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Los_Angeles",
  "UTC",
];

function OrganizationCard() {
  const { user, can, refresh } = useAuth();
  const [name, setName] = useState(user.organization.organization_name);
  const [timezone, setTimezone] = useState(user.organization.timezone);
  const mutation = useMutation({
    mutationFn: () => updateOrganization({ name: name.trim(), timezone }),
    onSuccess: async () => {
      toast.success("Organization updated");
      await refresh();
    },
    onError: (error) =>
      toast.error("Could not update organization", { description: error.message }),
  });
  const zones = COMMON_ZONES.includes(timezone) ? COMMON_ZONES : [timezone, ...COMMON_ZONES];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Organization</CardTitle>
        <CardDescription>
          Schedules are stored in UTC and displayed in the organization timezone.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 sm:max-w-md">
        <div className="space-y-1.5">
          <Label htmlFor="org-name">Name</Label>
          <Input
            id="org-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={!can("manage_members")}
          />
        </div>
        <div className="space-y-1.5">
          <Label>Timezone</Label>
          <Select value={timezone} onValueChange={setTimezone} disabled={!can("manage_members")}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {zones.map((z) => (
                <SelectItem key={z} value={z}>
                  {z}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            Slug <span className="font-mono">{user.organization.organization_slug}</span>
          </p>
        </div>
        {can("manage_members") ? (
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending || !name.trim()}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Save
          </Button>
        ) : (
          <p className="text-xs text-muted-foreground">Only admins can change these settings.</p>
        )}
      </CardContent>
    </Card>
  );
}

function MembersCard() {
  const { user, can } = useAuth();
  const queryClient = useQueryClient();
  const members = useQuery(authQueries.members());
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Exclude<MembershipRole, "owner">>("editor");
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: authQueries.members().queryKey });

  const create = useMutation({
    mutationFn: () =>
      createMember({
        email: email.trim(),
        password,
        display_name: displayName.trim() || null,
        role,
      }),
    onSuccess: () => {
      toast.success("Member added");
      setEmail("");
      setDisplayName("");
      setPassword("");
      void invalidate();
    },
    onError: (error) => toast.error("Could not add member", { description: error.message }),
  });
  const changeRole = useMutation({
    mutationFn: ({ userId, next }: { userId: string; next: Exclude<MembershipRole, "owner"> }) =>
      updateMemberRole(userId, { role: next }),
    onSuccess: () => {
      toast.success("Role updated");
      void invalidate();
    },
    onError: (error) => toast.error("Could not change role", { description: error.message }),
  });
  const signOutEverywhere = useMutation({
    mutationFn: (userId: string) => revokeMemberSessions(userId),
    onSuccess: () => toast.success("Member signed out on every device"),
    onError: (error) => toast.error("Could not revoke sessions", { description: error.message }),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Members</CardTitle>
        <CardDescription>
          viewer reads · editor creates and edits · admin approves, manages accounts and members ·
          owner is the organization root.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {members.isPending ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : members.isError ? (
          <Alert variant="destructive">
            <TriangleAlert />
            <AlertTitle>Could not load members</AlertTitle>
            <AlertDescription>{members.error.message}</AlertDescription>
          </Alert>
        ) : (
          <ul className="divide-y rounded-lg border">
            {members.data.map((m) => (
              <li key={m.user_id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">
                    {m.display_name ?? m.email}
                    {m.user_id === user.id ? (
                      <span className="ml-1 text-xs text-muted-foreground">(you)</span>
                    ) : null}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {m.email} · last sign-in {formatDateTime(m.last_login_at)}
                  </p>
                </div>
                {can("manage_members") &&
                m.user_id !== user.id &&
                (m.role !== "owner" || user.organization.role === "owner") ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => signOutEverywhere.mutate(m.user_id)}
                    disabled={signOutEverywhere.isPending}
                    title="Revoke every session of this member"
                  >
                    Sign out everywhere
                  </Button>
                ) : null}
                {can("manage_members") && m.role !== "owner" && m.user_id !== user.id ? (
                  <Select
                    value={m.role}
                    onValueChange={(v) =>
                      changeRole.mutate({
                        userId: m.user_id,
                        next: v as Exclude<MembershipRole, "owner">,
                      })
                    }
                  >
                    <SelectTrigger className="w-32" size="sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ASSIGNABLE_ROLES.map((r) => (
                        <SelectItem key={r} value={r}>
                          {r}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Badge variant="secondary" className="capitalize">
                    {m.role}
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        )}
        {can("manage_members") ? (
          <form
            className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <p className="text-sm font-medium sm:col-span-2">Add a member</p>
            <div className="space-y-1.5">
              <Label htmlFor="m-email">Email</Label>
              <Input
                id="m-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="m-name">Display name</Label>
              <Input
                id="m-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="m-pw">Initial password (min 10 chars)</Label>
              <Input
                id="m-pw"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Role</Label>
              <Select
                value={role}
                onValueChange={(v) => setRole(v as Exclude<MembershipRole, "owner">)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ASSIGNABLE_ROLES.map((r) => (
                    <SelectItem key={r} value={r}>
                      {r}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="sm:col-span-2">
              <Button type="submit" disabled={create.isPending || !email || password.length < 10}>
                {create.isPending ? <LoaderCircle className="animate-spin" /> : <UserPlus />} Add
                member
              </Button>
            </div>
          </form>
        ) : null}
      </CardContent>
    </Card>
  );
}

function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const mutation = useMutation({
    mutationFn: () => changePassword({ current_password: current, new_password: next }),
    onSuccess: () => {
      toast.success("Password changed. Other sessions were signed out.");
      setCurrent("");
      setNext("");
      setConfirm("");
    },
    onError: (error) => toast.error("Could not change password", { description: error.message }),
  });
  const valid = current && next.length >= 10 && next === confirm;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Your password</CardTitle>
        <CardDescription>
          At least 10 characters. Changing it signs out your other sessions.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="grid gap-3 sm:max-w-md"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid) mutation.mutate();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="pw-current">Current password</Label>
            <Input
              id="pw-current"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="pw-next">New password</Label>
            <Input
              id="pw-next"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="pw-confirm">Confirm new password</Label>
            <Input
              id="pw-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </div>
          <div>
            <Button type="submit" disabled={!valid || mutation.isPending}>
              {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null} Change
              password
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export function SettingsPanel() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="Organization, team, content taxonomy, folder mapping and your account."
      />
      <Tabs defaultValue="organization">
        <TabsList className="flex-wrap">
          <TabsTrigger value="organization">Organization</TabsTrigger>
          <TabsTrigger value="members">Members</TabsTrigger>
          <TabsTrigger value="categories">Categories</TabsTrigger>
          <TabsTrigger value="folders">Folder mapping</TabsTrigger>
          <TabsTrigger value="password">Password</TabsTrigger>
        </TabsList>
        <TabsContent value="categories" className="pt-4">
          <CategoriesManager />
        </TabsContent>
        <TabsContent value="folders" className="pt-4">
          <FolderRulesManager />
        </TabsContent>
        <TabsContent value="organization" className="pt-4">
          <OrganizationCard />
        </TabsContent>
        <TabsContent value="members" className="pt-4">
          <MembersCard />
        </TabsContent>
        <TabsContent value="password" className="pt-4">
          <PasswordCard />
        </TabsContent>
      </Tabs>
    </div>
  );
}
