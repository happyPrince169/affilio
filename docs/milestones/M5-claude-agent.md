# M5 — Claude Agent

- **Status:** Planned
- **Depends on:** M0 accepted; M2 and M4 for grounding data; M3 for proposal targets;
  `ANTHROPIC_API_KEY` provided via environment (never committed)
- **Unlocks:** AI-assisted operations at scale

## Scope

A tool-based Claude assistant that helps with product research, content planning, caption
and hook generation, and optimization suggestions grounded in catalog and performance data.
The agent proposes; humans approve. Nothing the agent does publishes or mutates records
without an explicit approval step.

## Deliverables

- `agents` module: `agent_runs`, `agent_tool_calls`, `approvals`; tool registry where each
  tool wraps a module service function and declares whether it is read-only or requires
  approval.
- Anthropic API integration with structured JSON outputs (schema-validated), prompt
  caching where useful, and per-organization usage accounting.
- Workflows: "plan this week's content", "write caption variants for product X",
  "what should we post more of" (performance-grounded).
- Web: AI Assistant page with conversation, proposal cards, approve/reject, run history.
- Tests: tool schemas, approval gating, output validation with recorded fixtures (no live
  API calls in CI).

## Acceptance criteria

- Every mutating action appears as a proposal with a diff-like preview and requires approval.
- Agent outputs validate against schemas; invalid outputs are retried or surfaced, never
  applied.
- API key is read from the environment only; usage and cost are visible per organization.

## Known risks

- Model and SDK versions evolve; pin and document the model id in configuration.
- Hallucinated product claims → ground prompts in catalog data and require citations.
- Cost control at 100+ accounts → batch and cache, set budgets per organization.
