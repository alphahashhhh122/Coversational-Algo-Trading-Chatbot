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

### Iterative deep-research loop (`deep_research_report`)
`DeepResearchLoopService` (`services/deep_research_loop_service.py`) is the
first **LangGraph** agent — a genuine loop, not a fan-out. Its `StateGraph`:

    plan → gather → self-critique → (refine → self-critique)* → cited report

- **gather** reuses the parallel `ResearchAgentService` for the first pass.
- **self-critique** is a deterministic coverage analysis (which of the four core
  questions are answered, whether news is thin) — an honest self-assessment, not
  a hallucinated one — and decides whether another pass is worthwhile.
- **refine** does one bounded deepening pass: when data is thin it fetches and
  **cites** a public document via `KnowledgeService.search_and_fetch`
  (SSRF-guarded; fetched text is untrusted data, never instructions).
- the result carries an explicit **citation list** so every claim traces to a
  real source; unavailable data is still reported, never invented.

Bounded (`max_refines`, default 1), read-only, no order path. Routed from "deep
dive / full research report / in-depth research on SYMBOL"; a plain
"research/analyse SYMBOL" still gets the faster one-shot `deep_research`.
LangGraph earns its place here (loop control + conditional continuation) and is
reserved for the remaining iterative/durable phases below.

### Walk-forward validation (`validate_strategy_walk_forward`)
`StrategyOptimizerService.walk_forward` guards against overfitting: it splits the
stored history into an older *train* window and a newer *test* window, optimises
the grid on train, then evaluates that **same** config on the untouched test
window. The gap is the honest signal — a config that wins in-sample but loses
out-of-sample is reported as **overfit**, not celebrated. It reuses the fast
in-memory `simulate_only` (no persistence) so it stays chat-snappy; the Backtests
UI keeps the heavier, persisted `RobustnessService` for deeper experiments.
Routed from "walk-forward / out-of-sample / is that strategy robust for SYMBOL".

### Plan-and-execute comparison (`compare_investments`)
`PlanExecuteService` (`services/plan_execute_service.py`) is a **LangGraph**
plan → execute → synthesize agent: it plans one research step per symbol,
executes those read-only research sub-agents in parallel, then synthesises a
factual side-by-side — who leads on each fundamental ratio available for *every*
symbol (higher ROE/margins better, lower debt better), with technical trend
reported alongside. It names a leader only on a clear win, says "mixed" on a tie,
and reports missing data rather than inventing it.

Deliberately bounded and safe: **read-only, prepares no orders, gives no buy/sell
recommendation** — it compares real data and nothing more. Routed from "which is
stronger, A or B" / "compare A and B fundamentally"; a bare "compare A vs B"
still uses the faster side-by-side quote route. (Surfacing a *prepared order*
through the approval card via `interrupt()` is a deliberate future step — it
needs checkpoint/resume across HTTP requests and is kept out until it can be done
without weakening the human-approval guarantee.)

### Watch/monitor agent (`create_watch` / `check_watches` / `list_watches` / `remove_watch`)
`WatchService` (`services/watch_service.py`) watches *technical* conditions —
RSI below/above a level, or price vs its EMA20 — evaluated against real broker
candles (`ScreenerService.technical_snapshot`). It complements `PriceAlertService`
(raw price thresholds). `evaluate()` is exposed so a scheduled job can run it
proactively; the `check_watches` chat tool runs it on demand so it's usable and
demoable now. A watch **only ever notifies** — it never trades or prepares an
order — and a symbol with no data is reported as unchecked, never triggered.
Routed from "watch RELIANCE for RSI below 30" / "check my watches" / "stop
watching RELIANCE"; the existing watch*list* is untouched.

## Roadmap

The read-only agentic layer is in place. The remaining, deliberately-deferred
step is **action under approval**: letting a plan-and-execute run *prepare* an
order and surface it through the existing in-chat approval card via LangGraph
`interrupt()` (checkpoint/resume across HTTP requests). It stays out until it can
be done without weakening the standing guarantee — a human approves every order,
and no agent has a code path to the broker.
