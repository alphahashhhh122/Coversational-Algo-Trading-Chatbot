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

### Strategy-discovery agent (`run_strategy_optimization`)
`StrategyOptimizerService` backtests a small parameter grid for a template
(EMA/SMA crossover) over stored history, ranks the runs by return (flagging
too-few-trade overfits), and reports the leaderboard + best configuration.

To stay interactive it uses a new **`BacktestService.simulate_only`** — a fast
in-memory backtest that computes metrics without persisting the per-signal
audit trail (a full search dropped from >120s to ~0.1s). Only a strategy you
choose to *save* gets a full persisted run. Routed from "find / optimise / best
strategy for SYMBOL"; research-only, never trades; reports real metrics (it will
honestly say a template lost money rather than invent a winner).

### Long-term memory (`remember` / `recall_memory`)
`MemoryService` (`services/memory_service.py`) gives the agent layer a small,
honest persistent store (`agent_memory` table):

- **Notes** — free-text things the user asks it to keep (a preference, a risk
  profile). Stored verbatim; nothing is inferred. Routed from "remember that
  ...". Accumulate over time.
- **Research summaries** — after every `deep_research` run the agent saves a
  compact, factual one-liner for that symbol (sections covered, last price,
  trend). One per symbol (upsert). The research agent also reads the prior
  summary back into its findings so a fresh briefing knows it has looked before.

Recall (`what do you remember`, `what did we find on SYMBOL`) returns exactly
what was stored, with timestamps — never a fabrication. The **watchlist is not
duplicated here**; it stays in `watchlist_symbols` (`ScreenerService`) and memory
complements it.

## Roadmap

Later, iterative/durable agents are added behind a **LangGraph** runtime (chosen
for loop control, checkpoint/resume, `interrupt()` for human approval, and
streaming). LangGraph is introduced only when these are needed — not for the
parallel fan-out above.

1. **Deep-research loop** — plan → gather → self-critique → refine → cited report.
2. **Walk-forward validation** — extend the optimizer's best config with
   out-of-sample robustness checks (`RobustnessService`), checkpointed.
4. **Plan-and-execute** — decompose a task, run read-only sub-agents, and surface
   any prepared order through the existing in-chat approval card (`interrupt()`).
5. **Watch/monitor agent** — scheduled, proactive, approval-gated.
