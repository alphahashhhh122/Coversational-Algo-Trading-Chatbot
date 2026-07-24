# ATL Transition Plan

Evolving this platform — in place — from a single conversational trading
assistant into an **Agentic Trading Lab**: a multi-agent platform where agents
are registered, versioned, run, evaluated, and ranked, and the chat assistant
becomes the first registered agent rather than the whole product.

## Where we already stand (honest inventory)

Mapping the ATL reference architecture onto this codebase:

| ATL layer | Status | Our implementation |
| --- | --- | --- |
| Core Agent Runtime | ~80% | research/deep-research/optimizer/walk-forward/compare/watch services; `MemoryService` (state), orchestration router (decision), tool registry (sandbox), order-prep + human approval (execution adapter) |
| Infrastructure Adapters | ~90% | OpenAlgo/Dhan (market data + broker), Groq (LLM), news + web fetch (FinSearch), DuckDB (storage) |
| Platform & Access | ~60% | Auth + roles, OTel observability, audit trail. **Missing: an *agent* registry** (we have a *tool* registry) |
| Interfaces | ~50% | Web app + REST API + CLI. Missing: SDK packaging, Discord |
| Evaluation & Community | ~30% | Backtests, `simulate_only`, walk-forward verdicts, `ResponseEvaluator`. **Missing: agent-level scoring, leaderboard, gallery, contests** |

The pivot in one sentence: generalize "the assistant *is* the agent" into
"the runtime *hosts many* agents" — everything else (leaderboard, gallery,
contests) falls out of that one abstraction.

## Invariants (non-negotiable, carried forward)

- **No agent ever gets a code path to the broker.** Agents research, prepare,
  and notify; a human approves every order. Registration/ranking does not
  change this.
- **No fabrication.** An agent that lacks data reports the gap; evaluation
  scores honesty-compatible metrics (out-of-sample return, not in-sample hype).
- **DuckDB single-writer stays respected.** Concurrent agent runs go through
  the existing serialized job/task system, not parallel writers.

## Phase 1 — Agent contract + registry (the unlock)

Everything else depends on this; built first.

1. **`Agent` contract** (`agents/base.py`): `agent_id`, `name`, `version`,
   `description`, `capabilities`, `run(task: AgentTask) -> AgentResult`.
   `AgentResult` carries structured findings + the evidence needed to score it.
2. **`agents` table + `AgentRegistryService`**: register / version / list /
   get. Existing services get thin adapters and become the founding roster:
   `market_researcher` (ResearchAgentService), `deep_researcher`
   (DeepResearchLoopService), `strategy_discoverer` (StrategyOptimizerService),
   `strategy_validator` (walk_forward), `comparator` (PlanExecuteService),
   `sentinel` (WatchService). The chat assistant is registered as
   `conversational_assistant` — agent #1.
3. **API + UI**: `GET /agents`, `POST /agents/{id}/run`; an **Agents** panel
   (gallery seed) listing each agent with description, version, capabilities,
   and a run button. Chat routing gains "run the <agent> on X".

Exit criteria: 6–7 registered agents, discoverable via API/UI, each runnable
independently with a persisted `agent_runs` record.

## Phase 2 — Evaluation + leaderboard

1. **`agent_runs` / `agent_scores` tables** and an `AgentEvaluationService`:
   every run gets a scorecard. Strategy agents: out-of-sample return, drawdown,
   trade count, walk-forward verdict (reusing `simulate_only` + walk_forward —
   overfit configs score low, honestly). Research agents: section coverage,
   citation count, freshness (reusing `ResponseEvaluator` primitives).
2. **Leaderboard**: rank agents per category over a shared dataset + window
   (`GET /leaderboard`, UI panel). Rankings only ever compare like with like
   and display the evidence behind the score.

Exit criteria: every registered agent has a live score; leaderboard visible in
the UI; scores reproducible from stored evidence.

## Phase 3 — Runtime at scale

1. **Scheduled agent runs** via the existing job handlers (the
   `watch_evaluation` pattern): agents can run on cadence, results append to
   `agent_runs`, scores update.
2. **Per-agent budgets** (steps/time/LLM calls) enforced in the contract, and
   per-agent observability using the existing OTel + audit plumbing.
3. **Custom strategy specs as agents**: a saved NL-compiled strategy registers
   as a versioned agent, instantly scoreable — user-authored agents with zero
   new authoring surface.

## Phase 4 — Community & interfaces

1. **Gallery**: the Agents panel grows discover/reuse (clone an agent config,
   bump version).
2. **SDK**: a thin published Python client over the existing REST API with
   examples ("register an agent", "read the leaderboard").
3. **Contests**: a frozen dataset + deadline + leaderboard snapshot — mostly
   composition of Phase 2 pieces.
4. **Discord (optional)**: a bot forwarding to `/chat` and posting leaderboard
   updates.

## Sequencing rationale

Registry before leaderboard because a leaderboard without a contract ranks
nothing comparable; leaderboard before scale because scoring tells us which
agents are worth scheduling; community last because a gallery is only as good
as the evaluated agents in it. Each phase lands with the standing discipline:
full test suite green before commit, UI-verified live, honest degradation
everywhere.
