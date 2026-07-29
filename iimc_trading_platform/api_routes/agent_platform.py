"""The agent-platform routes: agents, leaderboard, arena, contests, supervisor.

Lifted out of ``create_app`` — 152 handlers in one 2,678-line function, each
closing over whichever of 83 service objects it happened to need. The closure
made those dependencies invisible; ``register`` states them.

This is a pure move: the handler bodies are unchanged, the paths are unchanged,
and the dependencies that were implicit are now named in one signature. What it
buys is that this group can be read, and its couplings counted, without holding
the whole application in your head.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from ..agents.base import AgentTask as _AgentTask
from ..api_models import (
    AgentRunRequest,
    ArenaEnrollRequest,
    ArenaSeasonRequest,
    AuthoredAgentRequest,
    CommitteeRequest,
    ContestRequest,
    DigestRequest,
    SupervisorSweepRequest,
)
from ..services import Principal
from ..tools.registry import _dataset_for_request as _resolve_dataset


def register(
    app: FastAPI,
    *,
    agents_by_key: dict[str, Any],
    agent_registry: Any,
    agent_evaluation: Any,
    authored_agents: Any,
    committee: Any,
    supervisor_service: Any,
    digest_service: Any,
    contest_service: Any,
    arena_service: Any,
    arena_datasets_for: Any,
    active_config: Any,
    viewer: Any,
    researcher: Any,
    agent_run_events: Any,
    evidence_dataset_id: Any,
) -> None:
    @app.get("/agents")
    def list_agents_endpoint(
        category: str | None = None,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return agent_registry.list(category=category)
    @app.get("/agents/{agent_id}")
    def get_agent_endpoint(
        agent_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        record = agent_registry.get(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Unknown agent")
        return record
    @app.get("/agents/{agent_id}/runs")
    def list_agent_runs_endpoint(
        agent_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return agent_registry.list_runs(agent_id)
    def _run_record_and_score(agent: Any, task: Any) -> dict[str, Any]:
        """Run an agent, persist the run, and score it from what was persisted.

        Shared by the plain and streaming endpoints so a streamed run is the
        same run — same record, same scorecard — and not a second code path
        that could drift away from it.
        """
        result = agent.run(task)  # kernel captures failures as status=failed
        run_id = agent_registry.record_run(agent, task, result)
        # Score straight from the recorded run so the leaderboard always
        # points at reproducible evidence.
        scorecard = agent_evaluation.score_run(
            {
                "status": result.status,
                "findings": result.findings,
                "evidence": result.evidence,
            },
            agent.category,
        )
        agent_evaluation.record_score(
            agent_id=agent.agent_id,
            version=agent.version,
            run_id=run_id,
            scorecard=scorecard,
            eval_dataset_id=evidence_dataset_id(result.evidence),
        )
        return {
            "run_id": run_id,
            "agent_id": agent.agent_id,
            "status": result.status,
            "findings": result.findings,
            "evidence": result.evidence,
            "gaps": result.gaps,
            "cost": result.cost,
            "scorecard": scorecard,
        }
    @app.post("/agents/{agent_id}/run")
    def run_agent_endpoint(
        agent_id: str,
        request: AgentRunRequest,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        agent = agents_by_key.get(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Unknown agent")
        return _run_record_and_score(
            agent,
            _AgentTask(
                task_type=request.task_type,
                symbol=request.symbol,
                symbols=tuple(request.symbols),
                exchange=request.exchange,
                params=request.params,
            ),
        )
    @app.get("/agents/{agent_id}/run/stream")
    def stream_agent_run_endpoint(
        agent_id: str,
        symbol: str | None = None,
        exchange: str = "NSE",
        task_type: str = "default",
        principal: Principal = Depends(viewer),
    ) -> StreamingResponse:
        """The same run as ``POST /run``, narrated while it happens.

        A GET because that is all ``EventSource`` speaks. The run is otherwise
        identical — it goes through the same helper, so streaming cannot
        produce a different answer from not streaming.
        """
        agent = agents_by_key.get(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Unknown agent")
        task = _AgentTask(
            task_type=task_type, symbol=symbol, exchange=exchange
        )
        return StreamingResponse(
            agent_run_events(agent, task, _run_record_and_score),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Tell any reverse proxy not to buffer: a buffered stream that
                # arrives all at once is the silence we were fixing.
                "X-Accel-Buffering": "no",
            },
        )
    @app.get("/leaderboard")
    def leaderboard_endpoint(
        category: str | None = None,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return agent_evaluation.leaderboard(category=category)

    @app.post("/leaderboard/rescore")
    def rescore_leaderboard_endpoint(
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        """Re-score every recorded run under the current rule.

        Reads only what the runs already recorded — nothing is re-executed and
        no number is invented. Runs whose evidence predates the current rule
        come back inconclusive, naming what they lack.
        """
        return agent_evaluation.rescore_history()

    @app.post("/agents/authored")
    def register_authored_agent_endpoint(
        request: AuthoredAgentRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            return authored_agents.register_from_spec(spec_id=request.spec_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    @app.get("/agents/authored/list")
    def list_authored_agents_endpoint(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return authored_agents.list_authored()
    @app.post("/committee")
    def committee_endpoint(
        request: CommitteeRequest,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return committee.run(
                request.symbol, request.exchange, tuple(request.members)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    @app.get("/supervisor/findings")
    def supervisor_findings_endpoint(
        include_acknowledged: bool = False,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return supervisor_service.list_findings(
            include_acknowledged=include_acknowledged
        )
    @app.post("/supervisor/sweep")
    def supervisor_sweep_endpoint(
        request: SupervisorSweepRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        agents = request.agents or [
            name
            for name in ("strategy_validator", "market_researcher")
            if name in agents_by_key
        ]
        return supervisor_service.sweep(agents, request.symbol)
    @app.post("/supervisor/findings/{finding_id}/acknowledge")
    def acknowledge_finding_endpoint(
        finding_id: str,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return supervisor_service.acknowledge(finding_id)
    @app.get("/supervisor/digest")
    def digest_latest_endpoint(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        """The most recent brief, or an explicit "none yet"."""
        latest = digest_service.latest()
        return latest or {"digest_id": None, "sections": [], "generated_at": None}
    @app.post("/supervisor/digest")
    def digest_generate_endpoint(
        request: DigestRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return digest_service.generate(symbol=request.symbol)
    @app.get("/contests")
    def list_contests_endpoint(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return contest_service.list()
    @app.post("/contests")
    def create_contest_endpoint(
        request: ContestRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        dataset_id = _resolve_dataset(
            active_config.database_path,
            symbol=request.symbol,
            exchange=request.exchange,
            raise_on_missing=False,
        )
        return contest_service.create(
            name=request.name,
            symbol=request.symbol,
            exchange=request.exchange,
            dataset_id=dataset_id,
            open_for_days=request.open_for_days,
        )
    @app.get("/contests/{contest_id}/results")
    def contest_results_endpoint(
        contest_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return contest_service.results(contest_id)
    @app.post("/contests/{contest_id}/close")
    def close_contest_endpoint(
        contest_id: str,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            return contest_service.close(
                contest_id, agent_evaluation.leaderboard(category="strategy")
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    @app.get("/arena/seasons")
    def list_arena_seasons_endpoint(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return arena_service.list_seasons()
    @app.post("/arena/seasons")
    def create_arena_season_endpoint(
        request: ArenaSeasonRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            return arena_service.create_season(
                name=request.name,
                symbol=request.symbol,
                symbols=request.symbols,
                exchange=request.exchange,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    @app.post("/arena/seasons/{season_id}/enroll")
    def enroll_arena_entry_endpoint(
        season_id: str,
        request: ArenaEnrollRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        return arena_service.enroll(
            season_id=season_id,
            agent_id=request.agent_id,
            strategy_name=request.strategy_name,
            parameters=request.parameters,
        )
    @app.get("/arena/seasons/{season_id}/standings")
    def arena_standings_endpoint(
        season_id: str,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return arena_service.standings(season_id)
    @app.post("/arena/seasons/{season_id}/tick")
    def arena_tick_endpoint(
        season_id: str,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        try:
            return arena_service.tick(
                season_id, datasets=arena_datasets_for(season_id)
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
