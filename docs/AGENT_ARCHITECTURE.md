# Agentic Layer

The platform is deterministic-first: most chat messages route to a single tool.
On top of that we are adding an **agent layer** for genuinely multi-step tasks,
without disturbing the fast path.

## Guardrails (apply to every agent)

- **No autonomous money movement.** Agents are read-only or may *prepare* an
  order; a human always approves before anything reaches the broker. There is no
  code path from an agent to order submission.
- **No fabrication.** Agents reason only over real tool outputs; unavailable
  data is reported, never invented.
- **Bounded.** Every agent has a step/time budget and a tool allow-list.
- **Audited.** Agent steps are persisted for review.

## Shipped

### Multi-analyst research agent (`deep_research`)
`ResearchAgentService` (`services/research_agent_service.py`) fans out — in
parallel via `asyncio` — to four read-only specialists and returns structured
findings:

- **valuation** — live quote (`InstrumentDiscoveryService`)
- **fundamentals** — ratios from imported statements (`FundamentalsService`)
- **technicals** — RSI/EMA/trend from broker candles
  (`ScreenerService.technical_snapshot`)
- **news** — recent headlines (`MarketNewsService`)

The chat layer turns the findings into a balanced thesis grounded strictly in
that data (LLM), or a deterministic briefing when no LLM is configured. It is
registered as the `deep_research` tool and routed from "research / deep dive /
analyse SYMBOL". Purely read-only — no dependency beyond the standard library.

## Roadmap

Later, iterative/durable agents are added behind a **LangGraph** runtime (chosen
for loop control, checkpoint/resume, `interrupt()` for human approval, and
streaming). LangGraph is introduced only when these are needed — not for the
parallel fan-out above.

1. **Long-term memory** — watchlist, risk profile, past reports.
2. **Deep-research loop** — plan → gather → self-critique → refine → cited report.
3. **Strategy discovery/optimization** — propose → backtest → evaluate → refine →
   walk-forward validate, checkpointed.
4. **Plan-and-execute** — decompose a task, run read-only sub-agents, and surface
   any prepared order through the existing in-chat approval card (`interrupt()`).
5. **Watch/monitor agent** — scheduled, proactive, approval-gated.
