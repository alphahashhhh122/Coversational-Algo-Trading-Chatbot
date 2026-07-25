"""Registry and run history for ATL agents.

Persists the roster in the ``agents`` table (append-only versioning via the
(name, version) uniqueness) and every execution in ``agent_runs`` with its
findings, evidence, and honest gaps — the raw material the evaluation engine
(Phase 2) scores. Storage only; running an agent happens in the kernel.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from ..agents.base import Agent, AgentResult, AgentTask


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AgentRegistryService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    # -- roster ---------------------------------------------------------------

    def sync_roster(self, agents: list[Agent], *, author: str = "platform") -> int:
        """Upsert the shipped roster (idempotent across restarts)."""

        now = _utc_now()
        con = connect(self.db_path)
        try:
            for agent in agents:
                # Delete-then-insert: the table has two unique constraints
                # (agent_id PK and (name, version)), so DuckDB can't infer a
                # conflict target for INSERT OR REPLACE.
                con.execute(
                    "DELETE FROM agents WHERE agent_id = ?", [agent.agent_id]
                )
                con.execute(
                    "INSERT INTO agents VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                    [
                        agent.agent_id,
                        agent.name,
                        agent.version,
                        agent.category,
                        agent.description,
                        json.dumps(list(agent.capabilities)),
                        json.dumps({}),
                        author,
                        now,
                    ],
                )
        finally:
            con.close()
        return len(agents)

    def list(self, *, category: str | None = None) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT a.agent_id, a.name, a.version, a.category, a.description,
                       a.capabilities_json, a.status, a.created_at,
                       (SELECT COUNT(*) FROM agent_runs r
                         WHERE r.agent_id = a.agent_id) AS run_count,
                       (SELECT MAX(r.started_at) FROM agent_runs r
                         WHERE r.agent_id = a.agent_id) AS last_run_at
                FROM agents a
                ORDER BY a.category, a.name
                """
            ).fetchall()
        finally:
            con.close()
        agents = [
            {
                "agent_id": r[0],
                "name": r[1],
                "version": r[2],
                "category": r[3],
                "description": r[4],
                "capabilities": json.loads(r[5]),
                "status": r[6],
                "created_at": _iso(r[7]),
                "run_count": int(r[8] or 0),
                "last_run_at": _iso(r[9]),
            }
            for r in rows
            if category is None or r[3] == category
        ]
        return {"agents": agents}

    def get(self, agent_id: str) -> dict[str, Any] | None:
        listed = self.list()["agents"]
        for item in listed:
            if item["agent_id"] == agent_id or item["name"] == agent_id:
                return item
        return None

    # -- runs -----------------------------------------------------------------

    def record_run(
        self, agent: Agent, task: AgentTask, result: AgentResult
    ) -> str:
        run_id = f"arun_{uuid.uuid4().hex[:12]}"
        con = connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO agent_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    agent.agent_id,
                    agent.version,
                    json.dumps(
                        {
                            "task_type": task.task_type,
                            "symbol": task.symbol,
                            "symbols": list(task.symbols),
                            "exchange": task.exchange,
                            "params": task.params,
                        }
                    ),
                    result.status,
                    json.dumps(result.findings, default=str),
                    json.dumps(result.evidence, default=str),
                    json.dumps(result.gaps),
                    json.dumps(result.cost),
                    _utc_now(),
                    _utc_now(),
                ],
            )
        finally:
            con.close()
        return run_id

    def list_runs(self, agent_id: str, *, limit: int = 20) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT run_id, agent_id, version, task_json, status,
                       findings_json, evidence_json, gaps_json, cost_json,
                       started_at
                FROM agent_runs
                WHERE agent_id = ?
                   OR agent_id IN (SELECT agent_id FROM agents WHERE name = ?)
                ORDER BY started_at DESC LIMIT ?
                """,
                [agent_id, agent_id, limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "runs": [
                {
                    "run_id": r[0],
                    "agent_id": r[1],
                    "version": r[2],
                    "task": json.loads(r[3]),
                    "status": r[4],
                    "findings": json.loads(r[5]) if r[5] else {},
                    "evidence": json.loads(r[6]) if r[6] else [],
                    "gaps": json.loads(r[7]) if r[7] else [],
                    "cost": json.loads(r[8]) if r[8] else {},
                    "started_at": _iso(r[9]),
                }
                for r in rows
            ]
        }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)
