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
watching RELIANCE"; the existing watch*list* is untouched. Watches are also
**visible in the UI** — a Watches panel in the Account view (`GET /watches`,
`POST /watches/check`, `DELETE /watches/{id}`) lists them with a "Check now"
button and per-row remove — and `evaluate()` is registered as a `watch_evaluation`
job handler so a scheduler can run it proactively.

## The ATL agent platform (shipped: kernel + evaluation)

The services above are now also exposed as **registered agents** with a uniform
contract, so they can be listed, run, recorded, and ranked. See
`docs/ATL_TRANSITION.md` for the full plan.

### Agent kernel (`agents/`)
`Agent` / `AgentTask` / `AgentResult` (`agents/base.py`) define the contract:
every run returns a status, structured findings, the **evidence** needed to
score it, and an honest list of **gaps**. `ServiceAgent` adapts an existing
*tool* (not a raw service) so an agent run gets the same validation, dataset
resolution, and auto-fetch as a chat request. The wrapper adds timing, budget
enforcement (`AgentBudget` — an overrun downgrades to `partial`, it does not
silently truncate), and converts exceptions into `status="failed"` with the
reason recorded rather than raising.

The founding roster (`agents/roster.py`): `market_researcher`,
`deep_researcher`, `strategy_discoverer`, `strategy_validator`, `comparator`,
`sentinel`, and `conversational_assistant` — the chat assistant is registered
agent #7, not a special case.

`AgentRegistryService` persists the roster (`agents`) and every execution
(`agent_runs`) with its findings, evidence, and gaps.

### Evaluation & leaderboard (`agent_evaluation_service.py`)
Scores are computed **from recorded runs**, so every leaderboard cell traces
back to an `agent_runs` row:

- **strategy** — out-of-sample only. In-sample returns never reach a ranking;
  the OOS return is weighted by the walk-forward verdict, so an `overfit`
  config is penalised (0.25×) versus one that `holds_up` (1.0×). Fewer than
  three OOS trades is `inconclusive` — unranked, never a flattering zero.
- **research** — coverage of the four core questions plus resolvable
  citations (bounded bonus, so link-spam can't win). Eloquence isn't measured.
- **monitor** — precision of fired conditions weighted by data coverage;
  unavailable data lowers coverage, it is not counted as a false fire.

`GET /leaderboard` returns `ranked` plus a separate `unranked` list with the
reason each agent is inconclusive. The **Agents** tab renders both, with each
row showing the run id (and dataset id) it traces to.

#### Scoring versions, and why a leaderboard can lie without one
Every scorecard records the `scoring_version` that produced it, and the rule
has changed three times: v1 ranked raw out-of-sample return, v2 switched to
excess over buy-and-hold with risk penalties, v3 fixed the Sharpe basis. A
composite from one rule ranked directly against a composite from another is
meaningless, and nothing on screen said so — five stored scores predated
versioning entirely.

`POST /leaderboard/rescore` (also `rescore-leaderboard` on the CLI, and
`client.rescore()`) recomputes every stored score from the evidence already in
`agent_runs`. Nothing is re-executed and no number is invented. What it cannot
do is make an old run comparable: a run that never captured a benchmark has no
benchmark to recover, so it comes back **inconclusive** naming what it lacks
rather than being ranked on partial data. Re-running the agent is what makes it
rankable. Rows scored under an older rule are flagged in the Leaderboard view.

**No benchmark, no rank.** Falling back to raw return used to look like graceful
degradation, but it puts two different quantities in one column: +5% raw and
+5% *excess over holding* are not the same claim, and the table sorts them as
though they were.

#### What "Sharpe" counts here
Sharpe and Sortino are measured over **every day in the test window**, with
days the strategy didn't trade contributing zero-return observations.

This is worth stating because getting it wrong is easy and invisible. Sharpe
is a mean-over-deviation ratio annualised by √252, which assumes consecutive
daily observations; feeding it only the days a strategy happened to trade
measures something else entirely and flatters exactly the strategies that
trade least. A configuration trading five days in a hundred scored 32.7 under
that mistake where the honest figure was 3.3 — and a Sharpe above about 3 is
already rare enough to disbelieve.

Callers pass the candle timestamps as `session_dates`. Without them the older
basis still works but is labelled `traded_days_only` in the result rather than
passed off as daily returns. Where the sample cannot support a ratio at all —
no trades, a single observation, no losing day for Sortino — the value is
`None`, not `0.0`: a zero reads as "no risk-adjusted edge" when the truth is
"not computable".

### The Arena (`arena_service.py`)
Season-based competition on **real market data** through an internal simulated
ledger (`BacktestService.simulate_only` — the same fill/fee/slippage machinery
as research backtests). There is **no broker code path in the arena at all,
not even a sandbox one** — that is what lets agents compete autonomously
without weakening the human-approval guarantee. A test asserts this against
the parsed AST: no broker client import, no order-placement call.

Enrolled agents each start from the same bankroll; a tick recomputes every
entry on the season's dataset and snapshots equity/return/drawdown/trades.
When data is unavailable the day is recorded as `data_missing` for that entry
— never interpolated — and such entries appear under `unavailable` in the
standings rather than as a zero that looks like a real result. Re-ticking the
same day overwrites rather than duplicating.

`arena_daily_tick` is registered as a job handler (alongside
`watch_evaluation`), so seasons can advance on a schedule through the existing
serialized job system.

### Authored agents (`authored_agent_service.py`)
A plain-English strategy becomes a first-class agent — no code written by the
user, none generated by the platform.

**Why it's safe:** the text compiles to a **rule spec** (plain data:
indicators, comparisons, thresholds) which the deterministic `RuleSpecStrategy`
runtime interprets. No `eval`, no generated source, no import of user content —
authoring cannot execute arbitrary code. Specs are validated against the
runtime's declared capabilities before they can run; unsupported indicators are
refused rather than half-working.

**Versioning is append-only.** Editing creates `v2` instead of mutating `v1`,
so old runs and scores keep pointing at the exact spec that produced them and a
leaderboard entry can never silently change meaning.

Authored agents are walk-forwarded through `walk_forward_spec`, which returns
the *same shape* as the built-in walk-forward — so they are scored by exactly
the same out-of-sample rules, on the same leaderboard, with no special case.

### Committee mode (`committee_service.py`)
A LangGraph `plan → convene → synthesize` graph that puts a question to several
registered agents in parallel and returns one brief with **per-agent
attribution**.

The design choice that matters: when members disagree the committee **reports
the conflict** rather than averaging it. A blended number would hide the most
useful signal a multi-agent system can produce — that the evidence is mixed.
Members that fail or have no directional read become explicit `gaps`, not
silent drops.

### MCP surface
`iimc_trading_platform.mcp_server` speaks JSON-RPC 2.0 over stdio, exposing the
researcher-level tools **plus** `list_agents`, `run_agent`, and
`get_leaderboard`, so any MCP client (Claude Desktop, Claude Code) can browse
and run the agent platform. Approval and order submission are outside the
exposed subset; a test asserts no callable on that surface contains `approve`,
`order`, `submit`, `execute`, or `trade`. See `docs/YOUR_TASKS.md` for setup.

API: `GET /agents`, `GET /agents/{id}`, `GET /agents/{id}/runs`,
`POST /agents/{id}/run`, `POST /agents/authored`, `GET /agents/authored/list`,
`POST /committee`, `GET /leaderboard`, `GET|POST /arena/seasons`,
`POST /arena/seasons/{id}/enroll`, `POST /arena/seasons/{id}/tick`,
`GET /arena/seasons/{id}/standings`.

### Autonomy: the supervisor (`supervisor_service.py`)
The platform watches its own agents. On a schedule (`agent_supervisor_sweep`,
every 6h) it re-runs key agents — so the leaderboard reflects current data
rather than whenever someone last clicked — then compares each new score
against that agent's own history and raises a finding when something moved
materially (>25%; smaller wobbles are noise, and a supervisor that cries wolf
gets ignored).

**It flags; it never acts.** No retiring, no reconfiguring, and certainly no
trading. An autonomous system that acts on its own conclusions needs a far
stronger correctness guarantee than "the metric moved"; one that surfaces
*"this agent's out-of-sample edge has halved since last week"* is useful **and**
safe, because a human still decides. A test asserts the service exposes no
retire/disable/trade/order/approve surface at all.

Findings persist (`supervisor_findings`), are de-duplicated per agent+kind so a
repeating condition doesn't spam, and can be acknowledged. `becoming
unscorable` is itself a finding — an agent that could be scored before and
can't now usually means its data went away.

Budgets (`AgentBudget` + `BudgetLedger`) cap seconds, steps, and LLM calls.
Exceeding a cap yields `partial` plus a gap naming the cap, so a scheduled run
that hit a wall is distinguishable from one that genuinely finished.

#### Watching the data, not just the scores
An agent's score can only be as good as the data underneath it, so the sweep
also checks the datasets agents depend on (`FreshnessService.assess`) and
raises `data_stale` — or `data_unassessable`, when the dataset has no freshness
policy, because "we could not check" is a different fact from "it is fine".

This is where the **one exception** to flag-never-act lives: when a dataset is
stale the supervisor may **enqueue a refresh job**. Fetching market data is the
only corrective step with no financial consequence, so it needs no human in the
loop. Everything else still only flags. The hook is injected at the call site
(`enqueue_refresh=`) rather than built into the service, so the single action it
can take is visible in `api.py` instead of buried. Without that hook the
supervisor still reports the staleness and says no refresh path is configured.

#### Regime awareness
`check_regime` compares recent return volatility against the preceding stretch
of the same series; a ratio outside 0.67–1.5× (over at least 40 observations)
raises `regime_shift`. The ordering inside `sweep()` is deliberate: **regime is
checked first, then agents are re-run, then drift is compared.** The re-run *is*
the re-validation — those scores are earned under the new regime, and the drift
check that follows compares them against scores earned under the old one, so an
edge that was regime-specific surfaces as a degradation with the regime finding
sitting beside it as the explanation. The finding records
`revalidated_in_this_sweep` so it never implies work that didn't happen.

### Streaming progress (`progress.py`, `GET /agents/{id}/run/stream`)
A deep-research loop takes ten seconds or more, and an unnarrated wait is
indistinguishable from a hang. `GET /agents/{id}/run/stream` returns Server-Sent
Events: `started`, then a `progress` frame per step, then `result` (or `failed`).
Quiet stretches emit a `:` keep-alive comment so an idle connection is not
mistaken for a dropped one.

SSE rather than WebSockets: progress is one-directional, it works with the
existing sync handlers, and it adds no dependency. The run happens on a worker
thread while the generator drains its progress queue — the work is synchronous
and would otherwise emit nothing until it finished, which is the silence being
fixed. Both endpoints go through one `_run_record_and_score` helper, so a
streamed run is the *same* run: recorded once, scored identically.

Progress is published through a **context variable** (`progress.report`) rather
than a callback threaded through every layer. The code that knows about
progress — a LangGraph node, a committee member returning — sits below the agent
kernel, the tool registry, and a Pydantic-validated handler; a parameter would
mean changing every tool handler signature for pure observability. With no sink
installed `report` is a no-op, and a failing sink is swallowed, because
observability must never change behaviour. Context variables deliberately do not
cross into new threads: the worker installs the sink itself, so a stream only
ever sees its own run.

The browser reads the stream over `fetch` rather than `EventSource` — the latter
cannot send an `Authorization` header, and a token has no business in a query
string.

### The daily digest (`daily_digest_service.py`)
Findings arrive continuously; a platform that emits fifty notifications a day is
one you stop reading. The `daily_digest` job (24h) composes them into **one
attributed brief** answering three questions in order: *what changed* (material
score moves + the current top of the leaderboard), *what's stale* (freshness
findings + coverage gaps), and *what degraded* — pulled out separately so good
news cannot bury bad news.

Every line names its source: a supervisor finding id, or the run id behind a
ranked number. Sections whose collaborator was unavailable say so as a gap
rather than rendering empty, because a blank section reads as "nothing to
report" when the truth is "we could not look". Pass a symbol and the brief adds
a committee read, with any disagreement carried through unresolved.

The digest is a **view**: it reads what the supervisor already found and takes
no action of its own. `GET|POST /supervisor/digest`, `client.digest()` /
`client.generate_digest()` in the SDK, and the `get_digest` MCP tool.

## Roadmap

The read-only agentic layer is in place. The remaining, deliberately-deferred
step is **action under approval**: letting a plan-and-execute run *prepare* an
order and surface it through the existing in-chat approval card via LangGraph
`interrupt()` (checkpoint/resume across HTTP requests). It stays out until it can
be done without weakening the standing guarantee — a human approves every order,
and no agent has a code path to the broker.
