from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from iimc_trading_platform.api import create_app


def main() -> int:
    client = TestClient(create_app())
    requests = [
        (
            "liveness",
            "get",
            "/live",
            None,
        ),
        (
            "readiness",
            "get",
            "/ready",
            None,
        ),
        (
            "datasets",
            "get",
            "/datasets",
            None,
        ),
        (
            "platform_status",
            "get",
            (
                "/platform/status?symbol=RELIANCE&exchange=NSE"
                "&asset_class=equity&interval=5m"
                "&start_date=2026-01-01&end_date=2026-01-31"
            ),
            None,
        ),
        (
            "openalgo_monitor",
            "get",
            "/platform/openalgo/monitor",
            None,
        ),
        (
            "market_news_status",
            "get",
            "/market-news/status",
            None,
        ),
        (
            "freshness",
            "get",
            (
                "/datasets/NIFTY_MONTH_E1_5m_options/freshness"
                "?purpose=current_market"
            ),
            None,
        ),
        (
            "knowledge",
            "post",
            "/knowledge/search",
            {
                "query": (
                    "Why does the LLM call typed tools instead of the database?"
                ),
                "limit": 3,
            },
        ),
        (
            "chat",
            "post",
            "/chat",
            {
                "session_id": "real_smoke_session",
                "message": "Explain the platform architecture from the docs",
            },
        ),
    ]
    output = {}
    for name, method, path, payload in requests:
        request = getattr(client, method)
        response = (
            request(path, json=payload)
            if payload is not None
            else request(path)
        )
        response.raise_for_status()
        output[name] = response.json()
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
