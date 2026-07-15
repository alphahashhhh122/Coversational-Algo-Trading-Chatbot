from __future__ import annotations

import json
from pathlib import Path

from iimc_trading_platform.config import load_config
from iimc_trading_platform.services import PortfolioService


def main() -> None:
    config = load_config()
    service = PortfolioService(Path(config.database_path))
    portfolio = service.create(
        name="IIMC Portfolio Risk Workflow",
        starting_cash=1_000_000.0,
        created_by="portfolio_workflow",
    )
    decision = service.evaluate_and_reserve(
        portfolio_id=portfolio["portfolio_id"],
        symbol="NIFTY",
        side="BUY",
        quantity=1,
        price=24_000.0,
    )
    snapshot = service.apply_fill(
        portfolio_id=portfolio["portfolio_id"],
        reservation_id=decision["reservation_id"],
        reference_id="portfolio-workflow-buy-1",
        price=24_005.0,
        fees=2.4,
    )
    stopped = service.set_trading_enabled(
        portfolio_id=portfolio["portfolio_id"],
        enabled=False,
        reason="verify operator kill switch",
        changed_by="portfolio_workflow",
    )
    rejected = service.evaluate_and_reserve(
        portfolio_id=portfolio["portfolio_id"],
        symbol="NIFTY",
        side="BUY",
        quantity=1,
        price=24_000.0,
    )
    restored = service.set_trading_enabled(
        portfolio_id=portfolio["portfolio_id"],
        enabled=True,
        reason="workflow completed",
        changed_by="portfolio_workflow",
    )
    print(
        json.dumps(
            {
                "portfolio_id": portfolio["portfolio_id"],
                "approved_decision": decision,
                "post_fill": snapshot,
                "kill_switch_state": stopped["risk_control"],
                "blocked_decision": rejected,
                "restored_state": restored["risk_control"],
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
