from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..db import connect
from .health_service import foundation_health
from .operations_service import operational_summary


DATA_TYPE_BY_ASSET_CLASS = {
    "equity": "equity_ohlcv",
    "index": "index_ohlcv",
    "futures": "futures_ohlcv",
    "options": "options_ohlcv",
    "commodity": "commodity_ohlcv",
    "crypto": "crypto_ohlcv",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PlatformDashboardService:
    """Read-only dashboard contract for local platform operation evidence."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.db_path: Path = config.database_path

    def summary(self) -> dict[str, Any]:
        health = foundation_health(self.config)
        operations = operational_summary(self.config)
        counts = self._table_counts(
            [
                "data_catalog",
                "market_ohlcv",
                "strategy_runs",
                "strategy_signals",
                "risk_decisions",
                "order_events",
                "order_state_events",
                "trade_fills",
                "performance_summaries",
                "tool_calls",
                "audit_events",
                "openalgo_snapshots",
                "market_news_fetches",
                "market_news_articles",
                "knowledge_documents",
                "knowledge_chunks",
            ]
        )
        return {
            "generated_at": utc_now(),
            "status": (
                "ready_for_local_operation"
                if health["status"] == "healthy"
                else "needs_attention"
            ),
            "health": health,
            "operations": operations,
            "counts": counts,
            "latest_completed_run": self._latest_completed_run(),
            "asset_coverage": self._asset_coverage(),
            "execution_paths": {
                "research": {
                    "enabled": True,
                    "requires_human_approval": False,
                    "external_side_effects": False,
                },
                "backtest": {
                    "enabled": True,
                    "requires_human_approval": False,
                    "external_side_effects": False,
                },
                "paper_trading": {
                    "enabled": True,
                    "configured": bool(self.config.openalgo_api_key),
                    "mode": "openalgo_analyzer",
                    "requires_human_approval": self.config.require_paper_approval,
                    "external_side_effects": True,
                    "provider": "openalgo",
                },
                "live_trading": {
                    "enabled": self.config.allow_live_trading,
                    "requires_human_approval": True,
                    "external_side_effects": True,
                    "provider": "openalgo",
                },
            },
            "safety": {
                "live_trading_enabled": self.config.allow_live_trading,
                "live_trading_disabled": not self.config.allow_live_trading,
                "openai_key_configured": bool(self.config.openai_api_key),
                "openalgo_key_configured": bool(
                    self.config.openalgo_api_key
                ),
                "no_synthetic_fallback": True,
                "paper_submission_requires_approval": (
                    self.config.require_paper_approval
                ),
                "live_submission_requires_approval": True,
                "visible_in_openalgo_requires_openalgo_routed_activity": True,
            },
            "rag": {
                "document_count": counts["knowledge_documents"],
                "chunk_count": counts["knowledge_chunks"],
                "status": (
                    "healthy"
                    if counts["knowledge_documents"] and counts["knowledge_chunks"]
                    else "missing"
                ),
            },
            "market_news": {
                "configured": bool(
                    self.config.market_news_provider
                    and self.config.market_news_api_url
                ),
                "fetch_count": counts["market_news_fetches"],
                "article_count": counts["market_news_articles"],
                "no_synthetic_fallback": True,
            },
            "capabilities": {
                "chat_orchestration": True,
                "governed_data_catalog": True,
                "strategy_backtesting": True,
                "signal_risk_order_fill_audit": True,
                "performance_visualization": True,
                "openalgo_read_only_monitor": True,
                "human_approval_gate": True,
                "retrieval_and_evaluation": True,
                "multi_asset_readiness_validation": True,
                "provider_backed_news_when_configured": True,
            },
        }

    def operator_review(self) -> dict[str, Any]:
        run = self._latest_completed_run()
        run_id = run["run_id"] if run else None
        operator_goal = (
            "Explain how governed market data, strategy logic, risk decisions, "
            "orders, fills, broker readiness, and performance evidence work "
            "together inside the local platform."
        )
        return {
            "generated_at": utc_now(),
            "operator_goal": operator_goal,
            "latest_completed_run": run,
            "workflow": [
                {
                    "stage": "data",
                    "stored_in": [
                        "data_catalog",
                        "options_ohlcv",
                        "market_ohlcv",
                    ],
                    "purpose": "Locate governed market data and its quality/provenance.",
                },
                {
                    "stage": "entry_exit_signal",
                    "stored_in": ["strategy_signals"],
                    "purpose": "Persist every generated strategy signal with features and reason.",
                },
                {
                    "stage": "risk_management",
                    "stored_in": ["risk_decisions"],
                    "purpose": "Record approval/rejection, quantity limits, and policy checks.",
                },
                {
                    "stage": "order_management",
                    "stored_in": ["order_events", "order_state_events"],
                    "purpose": "Create append-only order state from the approved risk decision.",
                },
                {
                    "stage": "execution_and_performance",
                    "stored_in": ["trade_fills", "performance_summaries"],
                    "purpose": "Store simulated fills, P&L, drawdown, and summary metrics.",
                },
            ],
            "run_storage_evidence": (
                self._run_storage_counts(run_id) if run_id else {}
            ),
            "ui_actions": [
                {
                    "id": "open-runs",
                    "label": "Open Strategy Runs",
                    "target_view": "runs",
                    "requires_key": False,
                },
                {
                    "id": "open-openalgo",
                    "label": "Open OpenAlgo Monitor",
                    "target_view": "openalgo",
                    "requires_key": False,
                },
                {
                    "id": "open-operations",
                    "label": "Open Operations",
                    "target_view": "operations",
                    "requires_key": False,
                },
            ],
            "safety": {
                "research_not_advice": True,
                "live_trading_disabled": not self.config.allow_live_trading,
                "no_synthetic_fallback": True,
            },
        }

    def _table_counts(self, table_names: list[str]) -> dict[str, int]:
        con = connect(self.db_path)
        try:
            counts = {}
            for table_name in table_names:
                counts[table_name] = int(
                    con.execute(
                        f"SELECT COUNT(*) FROM {table_name}"
                    ).fetchone()[0]
                )
            return counts
        finally:
            con.close()

    def _latest_completed_run(self) -> dict[str, Any] | None:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT r.run_id, r.strategy_id, r.dataset_id, r.status,
                       r.execution_mode, r.started_at, r.finished_at,
                       p.total_trades, p.net_pnl, p.max_drawdown,
                       p.return_pct, p.metrics_json
                FROM strategy_runs AS r
                LEFT JOIN performance_summaries AS p ON p.run_id = r.run_id
                WHERE r.status = 'completed'
                ORDER BY r.finished_at DESC, r.started_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "strategy": row[1],
            "dataset_id": row[2],
            "status": row[3],
            "execution_mode": row[4],
            "started_at": row[5],
            "finished_at": row[6],
            "total_trades": row[7],
            "net_pnl": row[8],
            "max_drawdown": row[9],
            "return_pct": row[10],
        }

    def _run_storage_counts(self, run_id: str) -> dict[str, int]:
        con = connect(self.db_path)
        try:
            queries = {
                "strategy_runs": (
                    "SELECT COUNT(*) FROM strategy_runs WHERE run_id = ?"
                ),
                "strategy_signals": (
                    "SELECT COUNT(*) FROM strategy_signals WHERE run_id = ?"
                ),
                "risk_decisions": (
                    "SELECT COUNT(*) FROM risk_decisions WHERE run_id = ?"
                ),
                "order_events": (
                    "SELECT COUNT(*) FROM order_events WHERE run_id = ?"
                ),
                "order_state_events": (
                    """
                    SELECT COUNT(*)
                    FROM order_state_events AS s
                    JOIN order_events AS o ON o.order_id = s.order_id
                    WHERE o.run_id = ?
                    """
                ),
                "trade_fills": (
                    "SELECT COUNT(*) FROM trade_fills WHERE run_id = ?"
                ),
                "performance_summaries": (
                    "SELECT COUNT(*) FROM performance_summaries WHERE run_id = ?"
                ),
                "experiment_manifests": (
                    "SELECT COUNT(*) FROM experiment_manifests WHERE run_id = ?"
                ),
            }
            return {
                name: int(con.execute(sql, [run_id]).fetchone()[0])
                for name, sql in queries.items()
            }
        finally:
            con.close()

    def _asset_coverage(self) -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT data_type, COUNT(*), COALESCE(SUM(row_count), 0)
                FROM data_catalog
                GROUP BY data_type
                """
            ).fetchall()
        finally:
            con.close()
        rows_by_type = {
            row[0]: {
                "dataset_count": int(row[1]),
                "row_count": int(row[2]),
            }
            for row in rows
        }
        coverage = {}
        for asset_class, data_type in DATA_TYPE_BY_ASSET_CLASS.items():
            stored = rows_by_type.get(data_type, {})
            coverage[asset_class] = {
                "supported_by_architecture": True,
                "data_type": data_type,
                "dataset_count": stored.get("dataset_count", 0),
                "row_count": stored.get("row_count", 0),
                "local_data_available": bool(stored.get("dataset_count", 0)),
                "provider_validation_required": True,
            }
        return coverage
