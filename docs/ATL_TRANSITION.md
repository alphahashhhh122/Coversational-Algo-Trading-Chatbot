# Agentic Trading Lab — Master Plan

A 16-week plan to evolve this platform, in place, from a single conversational
trading assistant into an **Agentic Trading Lab (ATL)**: a multi-agent platform
where agents are registered, versioned, scheduled, evaluated on honest
out-of-sample evidence, ranked on a leaderboard, authored in plain English, and
run in a live paper-trading arena — with the chat assistant as registered
agent #1 rather than the whole product.

---

## 1. Vision, and what "best" means here

Plenty of projects bolt an LLM onto a backtester. The bar for this platform is
different, and measurable:

1. **Every number is reproducible.** A leaderboard score links to the stored
   run, the frozen dataset hash, and the parameters that produced it. No score
   exists without evidence.
2. **Honesty is the ranking function.** Strategy agents are ranked *only* on
   out-of-sample results; an overfit config is penalised, not celebrated.
   Research agents are scored on coverage and real citations, not eloquence.
3. **Safety is architectural, not procedural.** No agent — registered,
   scheduled, or user-authored — has a code path to the broker. The arena runs
   on an internal simulated ledger. A human approves every real order, always.
4. **A newcomer is productive in ten minutes.** Clone → run → see agents
   compete. The professor-evaluation bar generalises into the onboarding bar.
5. **Users author agents without writing code.** A plain-English strategy
   becomes a registered, versioned, ranked agent.

## 2. Invariants (carried forward, non-negotiable)

- **No autonomous broker orders.** Not live, not broker-sandbox. The arena
  uses our own in-process simulated ledger fed by real market data. The only
  path to a real order remains prepare → human approval in the web UI.
  (Approval is *never* exposed over Discord/SDK.)
- **No fabrication.** Missing data is reported as missing — in chat, in
  scorecards, in the arena (gaps are marked, never interpolated).
- **DuckDB single-writer respected.** All agent-run writes flow through the
  existing serialized job/task system; concurrency happens in gathering
  (asyncio), never in writing.
- **Full suite green before every commit; live UI verification for every
  user-visible change.** The discipline that got us here doesn't relax.

## 3. Asset inventory (what we build on)

| ATL layer | Status | Concrete modules |
| --- | --- | --- |
| Core Agent Runtime | ~80% | `research_agent_service`, `deep_research_loop_service` (LangGraph), `strategy_optimizer_service` (+ walk_forward), `plan_execute_service` (LangGraph), `watch_service`, `memory_service`; orchestration router; tool registry; approval-gated order prep |
| Infrastructure Adapters | ~90% | OpenAlgo/Dhan (quotes, candles, account), Groq (LLM), news + SSRF-guarded web fetch, DuckDB storage, `openalgo_history_import` |
| Platform & Access | ~60% | `auth_service` (roles), OTel + structured logging, `audit_service`; **missing: agent registry** |
| Interfaces | ~50% | Web app (vanilla JS), REST API, CLI; **missing: SDK, Discord** |
| Evaluation | ~30% | `backtest_service.simulate_only`, walk-forward verdicts, `RobustnessService`, `ResponseEvaluator`, evidence records; **missing: agent-level scoring, leaderboard** |
| Community | 0% | gallery, contests, docs site |

The pivot in one sentence: generalize "the assistant *is* the agent" into
"the runtime *hosts many* agents." Everything downstream — leaderboard, arena,
gallery, contests — is composition on top of that one abstraction.

---

## 4. Core technical designs

### 4.1 The Agent contract (`iimc_trading_platform/agents/`)

```python
class AgentTask:      # what an agent is asked to do
    task_type: str          # "research" | "discover" | "validate" | "compare" | "monitor" | ...
    symbol/symbols, exchange, dataset_id, params: ...
    budget: AgentBudget     # max_steps, max_seconds, max_llm_calls

class AgentResult:    # what every run must return
    status: "ok" | "partial" | "failed"
    findings: dict          # structured, category-specific payload
    evidence: list[Evidence]  # dataset hashes, citation URLs, run ids
    gaps: list[str]         # honest misses — feeds scoring
    cost: {steps, seconds, llm_calls}

class Agent(Protocol):
    agent_id: str; name: str; version: str
    category: "research" | "strategy" | "monitor" | "assistant"
    capabilities: tuple[str, ...]
    def run(self, task: AgentTask) -> AgentResult: ...
```

Existing services get **thin adapters** (no rewrites): the adapter translates
`AgentTask` → the service call and the service's dict → `AgentResult`. The
founding roster: `market_researcher`, `deep_researcher`, `strategy_discoverer`,
`strategy_validator`, `comparator`, `sentinel`, `conversational_assistant`.

### 4.2 Registry & persistence

New tables (via the existing `initialize_database` migration pattern):

```
agents        (agent_id PK, name, version, category, description,
               capabilities_json, config_json, author, status, created_at)
agent_runs    (run_id PK, agent_id, version, task_json, status,
               findings_json, evidence_json, gaps_json, cost_json,
               started_at, finished_at)
agent_scores  (score_id PK, agent_id, version, run_id, eval_dataset_id,
               metrics_json, composite, scored_at)
eval_datasets (eval_dataset_id PK, symbol, exchange, interval,
               start, end, row_count, content_hash, frozen_at)
```

`AgentRegistryService`: register / new-version / list / get / deactivate.
Versioning is append-only — old versions keep their runs and scores (lineage).

### 4.3 Evaluation methodology (where "best" is won or lost)

**Strategy agents** (discoverer, validator, user-authored):
- Evaluated *only* walk-forward: optimise on the frozen train window, score on
  the untouched test window.
- Metrics: OOS return %, OOS max drawdown, OOS Sharpe (computable from the
  `ResearchLedger` equity curve), trade count, walk-forward verdict.
- Composite: Sharpe-weighted OOS return, gated on `trades >= min_trades`
  (else *inconclusive*, unranked) and penalised on an `overfit` verdict.
  **In-sample numbers never appear on a leaderboard.**

**Research agents** (researcher, deep-researcher, comparator):
- Coverage (sections answered / expected), citation count with resolvable
  sources, data freshness, `ResponseEvaluator` grounding checks.
- No LLM-judge in v1 (bias risk); revisit in Phase 4 with human-spot-check
  calibration if wanted.

**Monitor agents** (sentinel): precision of fired conditions — when a watch
fires, the condition is re-verified against stored candles; false fires count
against the score.

**Score integrity rules:** ties display as ties; *inconclusive* is a state,
not a rank; every leaderboard cell click-throughs to its evidence.

### 4.4 The Arena (live paper-trading, safely)

A season-based competition that makes the leaderboard *alive*:

- **Internal simulated ledger only** — reusing the `ResearchLedger` fill/fee/
  slippage machinery from `simulate_only`. No broker order path exists, not
  even sandbox. This keeps the safety invariant absolute and lets agents
  "trade" autonomously without approval spam.
- Each enrolled strategy agent gets identical starting equity. A daily
  scheduled job (existing handler pattern, like `watch_evaluation`) pulls the
  day's real candles, feeds each agent's signals through its ledger, and
  appends `arena_trades` / equity snapshots.
- Season = configurable window (default 4 weeks). Season leaderboard = P&L,
  drawdown, Sharpe over the season. Broker-token gaps mark the day
  `data_missing` for everyone — never interpolated.
- Tables: `arena_seasons`, `arena_entries`, `arena_trades`,
  `arena_equity_daily`.

### 4.5 Scheduling, budgets, observability

- Scheduled runs ride the existing job system (`operations_service` handlers)
  — serialized, audited, restart-safe. New handlers: `agent_scheduled_run`,
  `arena_daily_tick`, `leaderboard_refresh`.
- `AgentBudget` enforced in the kernel wrapper (steps/time/LLM-call caps);
  exceeding budget → `partial` result, honestly labelled.
- Per-agent observability: every run emits structured logs + OTel spans tagged
  `agent_id/version`; the audit trail already captures tool-level actions.

### 4.6 Committee mode (multi-agent collaboration)

A LangGraph graph that fans a question out to relevant registered agents
(researcher + comparator + sentinel context), then a synthesis node produces a
joint brief **with per-agent attribution and surfaced disagreements** — the
committee never averages away a conflict, it reports it. Registered as
`research_committee`, so it is itself ranked like any other research agent.

### 4.7 API surface & SDK

```
GET  /agents                     list (filter by category/status)
GET  /agents/{id}                detail incl. versions + latest score
POST /agents/{id}/run            run with task payload (budget-capped)
GET  /agents/{id}/runs           run history + evidence
GET  /leaderboard?category=...   ranked, evidence-linked
GET  /arena/seasons/{id}         standings, equity curves
POST /agents/authored            register an NL-authored strategy agent
```

SDK = a thin, typed, pip-installable Python client over exactly this API
(requests-based, no heavy deps), shipped with a quickstart notebook:
list agents → run one → read the leaderboard → author an agent. The SDK can
**never** approve orders — that surface simply doesn't exist in the API it
wraps.

### 4.8 UI additions (same vanilla-JS app, no framework churn)

- **Agents** tab: gallery of registered agents — category, version,
  description, latest score, run button, run history.
- **Leaderboard** tab: category-switchable rankings; every score expands to
  its evidence; season standings + equity sparklines for the arena.
- Chat grows: "run the deep researcher on TCS", "who's top of the
  leaderboard", "enroll my strategy in the arena" (routes to registry/arena
  tools; same deterministic-first + LLM-fallback routing).
- Existing tabs untouched; client-facing rules hold (no jargon, no manual
  imports, never show wrong data).

---

## 5. Timeline — 16 weeks in 6 phases

Each phase ends demo-able, full-suite-green, pushed. Buffer is real.

| Phase | Weeks | Builds | Exit criteria (all verified live) | Demo moment |
| --- | --- | --- | --- | --- |
| **P0 Foundations** | 1 | Contract + schema design review; migrations; CI (GitHub Actions: fast job on push, full serial suite nightly); test-marker hygiene | CI green on a trivial PR; schema migrations apply cleanly to an existing DB | "Every push is tested automatically" |
| **P1 Agent Kernel** | 2–4 | `agents/` package, adapters for the 7 founding agents, `AgentRegistryService`, `agent_runs`, `/agents` API, Agents tab, chat routing | 7 agents registered/discoverable/runnable independently; every run persisted with evidence | "Open Agents tab, run the deep researcher, watch the run record appear" |
| **P2 Evaluation Engine** | 5–7 | Frozen `eval_datasets`, `AgentEvaluationService`, scorecards per category, `agent_scores`, `/leaderboard` API + tab, backfill | Every registered agent has a reproducible score; leaderboard live; in-sample numbers provably absent | "Click a leaderboard score → see its evidence" |
| **P3 Arena & Scale** | 8–10 | Simulated-ledger arena, seasons, daily tick job, scheduled agent runs, budgets, per-agent observability | A season running with ≥3 agents on live data; budget caps enforced; gaps marked honestly | "Agents competing on this week's real market" |
| **P4 Authoring & Committee** | 11–13 | NL strategy → registered agent (auto-version on edit), clone/config in gallery, committee mode, evaluation of authored + committee agents | A plain-English strategy appears on the leaderboard; committee brief with attribution + disagreements | "Describe a strategy in a sentence; watch it get ranked" |
| **P5 Community & Polish** | 14–16 | SDK published + quickstart, contests (frozen dataset, deadline, snapshot), docs pass, security review, perf pass, optional Discord bot (read/research only), recorded demo script | Clone→productive in <10 min measured; contest runs end-to-end; docs cover every surface | "Full tour: author → compete → rank → reproduce" |

**Weekly cadence:** build → full serial suite → live UI verification → commit
→ push. Nothing merges red. Each phase's last days are polish + demo hardening,
not new features.

## 6. Cross-cutting workstreams (run all 16 weeks)

- **Testing:** every new service lands with unit tests; routing changes always
  run the entire suite (standing rule); arena/eval get property-style tests
  (e.g., "no leaderboard row without an evidence link").
- **Docs:** `AGENT_ARCHITECTURE.md` stays the truth of what's *shipped*; this
  plan tracks what's *next*; phase completions update both (no stale docs).
- **Demo-readiness:** a `DEMO.md` script maintained from P1 on — the
  professor path is rehearsed continuously, not assembled at the end.
- **Security:** SSRF guards on all fetches (existing pattern), authored-agent
  configs validated against a strict schema (no arbitrary code execution — NL
  strategies compile to the existing rule-spec, never to Python), secrets
  never in repo or logs.

## 7. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| DuckDB single-writer contention as runs multiply | All writes via the serialized job queue (already the pattern); if it saturates, per-domain DB files before any engine swap |
| Groq rate limits / cost as agents scale | `AgentBudget` LLM-call caps + response caching for identical research sub-queries; deterministic paths preferred |
| Daily Dhan token expiry breaks scheduled runs | Runs degrade to `partial` with the gap recorded; arena marks `data_missing`; readiness check before each tick |
| Scope creep ("amazing" is elastic) | Phase gates: nothing from P(n+1) starts until P(n) exit criteria are demo-verified |
| Test suite time growth (15 min serial) | CI split (fast on push / full nightly + pre-merge); marker discipline; the suite stays serial per the known lock constraint |
| Leaderboard gaming (overfit chasing) | Frozen datasets + walk-forward-only scoring + overfit penalty; scoring code changes require a re-backfill so history stays comparable |

## 8. Open decisions (flag before their phase starts)

1. **Arena data cadence** — EOD daily ticks (robust, recommended) vs intraday
   5m (flashier, more token-fragile). Decide start of P3.
2. **Contest visibility** — private (professor demo) vs public repo
   invitational. Decide in P5.
3. **Discord bot** — build or skip; strictly read/research-only if built.
   Decide in P5.
4. **LLM-judge for research scoring** — off in v1; revisit with
   human-calibration in P4 if wanted.

## 9. What done looks like (measured, not vibes)

- ≥10 registered agents including ≥2 user-authored, each versioned with
  lineage.
- 100% of leaderboard entries click through to reproducible evidence.
- An arena season completed on real market data with zero autonomous broker
  orders (audit-verifiable).
- Committee mode producing attributed, disagreement-preserving briefs.
- SDK quickstart: clone → running agents → leaderboard read in <10 minutes.
- Full test suite green; every phase's demo path recorded in `DEMO.md`.
