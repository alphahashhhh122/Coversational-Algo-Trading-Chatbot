# OpenAlgo Project Context Capture

Captured into this workspace on 2026-07-01 from the Codex thread:

- Source thread title: `Open_Algo`
- Source thread id: `019e5873-f5b6-7730-9c56-2fc482681e8e`
- Source workspace: `C:\Users\nirwa\Documents\Codex\2026-05-24\do-u-have-any-context-of`
- Current copied workspace: `C:\Users\nirwa\Documents\Codex\2026-07-01\can-u\openalgo_project`

## Current Project Story

This is the IIM-C conversational algo-trading platform. It is a local-first research and controlled execution workspace where a user can ask natural-language questions for market research, registered-strategy backtesting, broker-backed instrument discovery, OpenAlgo readiness checks, dashboard monitoring, and governed paper/live order intents.

The strongest ownership line is:

> The LLM routes intent into typed tools, but backend services own validation, computation, persistence, risk checks, approvals, and execution boundaries.

Do not describe it as a loose ChatGPT wrapper. It is a backend trading platform with a conversational interface.

## Latest Resume Points

- Built a conversational algo-trading platform using Groq LLM orchestration, FastAPI, DuckDB, BM25 RAG, and OpenAlgo/Dhan APIs.
- Enabled chatbot workflows for market research, registered-strategy backtesting, broker-backed instrument discovery, monitoring, and dashboards.
- Designed governed LLM tool routing with Pydantic schemas, role gates, capability metadata, response grounding, audit trails, and HITL approvals.
- Validated on 66K+ options OHLCV rows with OpenAlgo quote/history/analyzer checks, NSE/NFO/MCX discovery, and API/service-layer tests.

Optional stronger variant for bullet 3 if space permits:

- Designed governed LLM tool routing with Pydantic schemas, role gates, capability metadata, response grounding, audit trails, HITL approvals, and approval-gated live order intents.

## Non-Negotiable Truths

- This project uses a custom LLM tool router/orchestrator, not LangGraph.
- LangGraph belongs to MASP, not this IIM-C platform.
- HITL is a backend role-gated approval layer, not a LangGraph interrupt node.
- Live trading is disabled by default through config and backend guards.
- Paper-trading sandbox workflows exist, but do not overclaim full broker-grade fill reconciliation.
- There is no formal measured LLM tool-routing accuracy yet. Treat that as future evaluation work.
- The project supports governed custom strategy specs; do not claim arbitrary safe execution of LLM-generated Python strategies.
- OpenAlgo/Dhan is the broker connectivity layer for quotes, history, analyzer checks, instrument discovery, account/order state, and trading workflows.

## Architecture In One Line

Chat UI -> FastAPI route -> Groq/custom orchestrator -> Pydantic tool registry -> domain service -> repository/provider -> DuckDB/OpenAlgo/Dhan/news provider -> grounded response plus dashboard/audit update.

## Important Files To Start From

- `README.md` - current product summary, capabilities, quick start, commands, and scope.
- `interview_prep_iimc_project.md` - most detailed ownership/interview preparation document.
- `docs/INTERVIEW_DEFENSE.md` - concise defense notes and practice questions.
- `docs/PROJECT_DEFENSE_TRACK.md` - preparation checklist for professor/recruiter ownership.
- `docs/PROFESSOR_DEMO_RUNBOOK.md` - demo flow.
- `docs/OPENALGO_SANDBOX_BRIDGE.md` - OpenAlgo sandbox/analyzer bridge notes.
- `docs/PRODUCTION_READINESS.md` - production-readiness framing.
- `iimc_trading_platform/api.py` - FastAPI route surface.
- `iimc_trading_platform/orchestration.py` - LLM/tool orchestration.
- `iimc_trading_platform/tools/registry.py` - governed tool contracts.
- `iimc_trading_platform/services/` - business services.
- `iimc_trading_platform/infrastructure/` - DuckDB and OpenAlgo integration.
- `tests/` - service/API tests supporting project claims.

## Useful Commands

```powershell
python -m pip install -e .
python -m iimc_trading_platform.cli init-db
python -m iimc_trading_platform.cli verify-foundation
python -m uvicorn iimc_trading_platform.asgi:app --reload --host 127.0.0.1 --port 8001
```

Focused tests:

```powershell
python -m pytest tests/test_api_chat.py tests/test_platform_api_routes.py tests/test_readiness_and_news.py -q
```

Full tests:

```powershell
python -m pytest
```

## Git And Copy Notes

- The copied project includes source, docs, tests, deployment references, data/artifacts, `.git`, `.env`, and generated/cache files from the source workspace.
- `interview_prep_iimc_project.md` is untracked in the copied Git repo because it was already untracked in the source repo.
- Do not print or paste `.env` values in chat. Treat copied credentials as local secrets.

## Project Review Questions To Keep Practicing

- Major problems faced and how they were solved.
- Why each tool and technology was chosen.
- Whether the objectives were met, and where the honest limits are.
- Lessons learned and what should be improved next.

Recommended answer frame:

- Problems: unstructured intent, unsafe trading actions, traceability, hallucinated results.
- Choices: FastAPI for typed APIs, Groq for low-latency orchestration, Pydantic for schema validation, DuckDB for local analytical persistence, BM25 for exact technical retrieval, OpenAlgo/Dhan for broker abstraction.
- Objectives: core research/demo objectives were met; full institutional production deployment, formal routing benchmarks, deeper live reconciliation, and generic no-code strategy composition are future work.
- Lessons: declare capability metadata early, evaluate LLM routing formally, keep LLMs away from execution, separate prototype claims from production claims, support custom strategies through validated specs rather than arbitrary generated code.
