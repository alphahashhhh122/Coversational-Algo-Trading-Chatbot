from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect
from .capability_coverage_service import CapabilityCoverageService
from .execution_readiness_service import ExecutionReadinessService
from .market_news_service import MarketNewsService


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ResearchService:
    def __init__(
        self,
        db_path: Path,
        capabilities: CapabilityCoverageService,
        news: MarketNewsService,
        execution_readiness: ExecutionReadinessService,
    ) -> None:
        self.db_path = db_path
        self.capabilities = capabilities
        self.news = news
        self.execution_readiness = execution_readiness

    def research_context(
        self,
        *,
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        return {
            "readiness": self.capabilities.platform_status(
                symbol=symbol,
                exchange=exchange,
                asset_class=asset_class,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
            ),
            "news": self.news.latest(limit=5),
            "research_not_advice": True,
            "no_synthetic_fallback": True,
        }

    def create_brief(
        self,
        *,
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
        start_date: str,
        end_date: str,
        created_by: str,
    ) -> dict[str, Any]:
        context = self.research_context(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
        readiness = self.execution_readiness.readiness(
            symbol=symbol,
            exchange=exchange,
            asset_class=asset_class,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
        data = context["readiness"]
        news = context["news"]
        stages = readiness["stages"]
        articles = news.get("articles", [])
        title = (
            f"{data['symbol']} {data['asset_class']} research brief "
            f"({data['interval']})"
        )
        payload = {
            "brief_id": f"brief_{uuid.uuid4().hex[:12]}",
            "title": title,
            "symbol": data["symbol"],
            "exchange": data["exchange"],
            "asset_class": data["asset_class"],
            "interval": data["interval"],
            "start_date": start_date,
            "end_date": end_date,
            "summary": [
                (
                    "Architecture supports this request."
                    if data["supported_by_architecture"]
                    else "Architecture does not support this asset request."
                ),
                (
                    f"Local governed data is available with "
                    f"{data['rows_available']} row(s)."
                    if data["local_dataset_exists"]
                    else "No local governed dataset is available."
                ),
                (
                    f"{len(articles)} stored provider-backed news article(s) "
                    "are available."
                ),
                (
                    "OpenAlgo is configured."
                    if readiness["openalgo"]["configured"]
                    else "OpenAlgo credentials are not configured."
                ),
            ],
            "stage_readiness": stages,
            "next_blocker": readiness["next_blocker"],
            "evidence": {
                "dataset_id": (
                    data.get("local_dataset") or {}
                ).get("dataset_id"),
                "news_article_ids": [
                    article["article_id"] for article in articles
                ],
                "news_configured": news["news_configured"],
                "provider_configured": data["provider_configured"],
                "openalgo_status": readiness["openalgo"]["status"],
            },
            "next_actions": [
                stage["next_action"]
                for stage in stages
                if not stage["can_start"] or stage["blockers"]
            ][:4],
            "guards": {
                "research_not_advice": True,
                "no_synthetic_fallback": True,
                "no_live_order_placement": True,
            },
        }
        self._store_brief(payload, created_by=created_by)
        return payload

    def list_briefs(self, limit: int = 20) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT brief_id, symbol, exchange, asset_class, interval,
                       start_date, end_date, title, payload_json,
                       created_by, created_at
                FROM research_briefs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            "briefs": [
                {
                    **json.loads(row[8]),
                    "created_by": row[9],
                    "created_at": row[10],
                }
                for row in rows
            ],
            "no_synthetic_fallback": True,
        }

    def _store_brief(
        self,
        payload: dict[str, Any],
        *,
        created_by: str,
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO research_briefs VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    payload["brief_id"],
                    payload["symbol"],
                    payload["exchange"],
                    payload["asset_class"],
                    payload["interval"],
                    payload["start_date"],
                    payload["end_date"],
                    payload["title"],
                    json.dumps(payload, sort_keys=True, default=str),
                    created_by,
                    utc_now(),
                ],
            )
        finally:
            con.close()
