import { BookOpen, Database, GitBranch, Terminal } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const steps = [
  {
    icon: Database,
    title: "Start PostgreSQL",
    command: "pnpm db:up",
    detail: "Runs the postgres:17 container defined in docker-compose.yml.",
  },
  {
    icon: GitBranch,
    title: "Apply migrations and seed",
    command: "pnpm db:migrate && pnpm db:seed",
    detail: "Creates the foundation tables and a development-only demo organization.",
  },
  {
    icon: Terminal,
    title: "Run the API",
    command: "pnpm dev:api",
    detail: "FastAPI on http://localhost:8000 with OpenAPI docs at /docs.",
  },
  {
    icon: BookOpen,
    title: "Read the architecture",
    command: "docs/architecture/overview.md",
    detail: "Module boundaries, database design, and milestone plan.",
  },
];

export function GettingStartedCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Local development</CardTitle>
        <CardDescription>Everything needed to bring the stack up on this machine.</CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="space-y-4">
          {steps.map((step, index) => {
            const Icon = step.icon;
            return (
              <li key={step.title} className="flex gap-3">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-accent text-accent-foreground">
                  <Icon className="size-3.5" />
                </span>
                <div className="min-w-0 space-y-1">
                  <p className="text-sm font-medium">
                    <span className="mr-1.5 font-mono text-xs text-muted-foreground">
                      {index + 1}.
                    </span>
                    {step.title}
                  </p>
                  <code className="block w-fit max-w-full truncate rounded-md bg-muted px-2 py-1 font-mono text-xs">
                    {step.command}
                  </code>
                  <p className="text-xs text-muted-foreground">{step.detail}</p>
                </div>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
