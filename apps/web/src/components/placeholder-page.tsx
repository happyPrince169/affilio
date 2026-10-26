import { Construction } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { MilestoneId } from "@/config/navigation";

interface PlaceholderPageProps {
  title: string;
  description: string;
  milestone: MilestoneId;
  milestoneTitle: string;
  plannedCapabilities: string[];
  dependencies?: string[];
}

/**
 * Shared placeholder for areas that are not implemented yet.
 * It must never display fabricated data or imply that an integration is connected.
 */
export function PlaceholderPage({
  title,
  description,
  milestone,
  milestoneTitle,
  plannedCapabilities,
  dependencies,
}: PlaceholderPageProps) {
  return (
    <div className="space-y-6">
      <PageHeader
        title={title}
        description={description}
        actions={
          <Badge variant="outline" className="font-mono">
            Planned · {milestone}
          </Badge>
        }
      />

      <Alert>
        <Construction />
        <AlertTitle>Not implemented yet</AlertTitle>
        <AlertDescription>
          This area is a placeholder. Its functionality is scheduled for milestone {milestone} (
          {milestoneTitle}). Nothing on this page is connected to a real data source or external
          platform.
        </AlertDescription>
      </Alert>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Planned capabilities</CardTitle>
            <CardDescription>Scope defined in docs/milestones/{milestone}-*.md</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {plannedCapabilities.map((capability) => (
                <li key={capability} className="flex items-start gap-2">
                  <span aria-hidden className="mt-1.5 size-1.5 shrink-0 rounded-full bg-brand/70" />
                  <span>{capability}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Dependencies</CardTitle>
            <CardDescription>What must exist before this area can be built.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <ul className="space-y-2 text-sm">
              {(dependencies ?? ["M0 Foundation accepted"]).map((dependency) => (
                <li key={dependency} className="flex items-start gap-2">
                  <span
                    aria-hidden
                    className="mt-1.5 size-1.5 shrink-0 rounded-full bg-muted-foreground/50"
                  />
                  <span>{dependency}</span>
                </li>
              ))}
            </ul>
            <Button asChild variant="outline" size="sm">
              <Link href="/">Back to dashboard</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
