from __future__ import annotations

import argparse
import json

from iimc_trading_platform.config import load_config
from iimc_trading_platform.domain import ExecutionMode
from iimc_trading_platform.infrastructure import (
    DuckDBAuditRepository,
    OpenAlgoClient,
    initialize_database,
)
from iimc_trading_platform.services import AuditService, RiskService
from iimc_trading_platform.services.sandbox_execution_service import (
    SandboxExecutionService,
)


CONFIRMATION = "I_UNDERSTAND_THIS_IS_AN_OPENALGO_SANDBOX_ORDER"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Approval-gated OpenAlgo analyzer workflow."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--symbol", required=True)
    prepare.add_argument("--exchange", required=True)
    prepare.add_argument("--side", choices=["BUY", "SELL"], required=True)
    prepare.add_argument("--product", choices=["MIS", "CNC", "NRML"], default="MIS")
    prepare.add_argument(
        "--order-type",
        choices=["MARKET", "LIMIT", "SL", "SL-M"],
        default="MARKET",
    )
    prepare.add_argument("--quantity", type=int, default=1)
    prepare.add_argument("--reference-price", type=float, required=True)
    prepare.add_argument("--limit-price", type=float)
    prepare.add_argument("--trigger-price", type=float)
    prepare.add_argument("--strategy", default="IIMC_Operator_Workflow")
    prepare.add_argument("--actor", default="operator")

    submit = subparsers.add_parser("approve-and-submit")
    submit.add_argument("--intent-id", required=True)
    submit.add_argument("--actor", required=True)
    submit.add_argument("--reason", required=True)
    submit.add_argument("--confirm", required=True)

    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--intent-id", required=True)
    reconcile.add_argument("--actor", default="system")

    args = parser.parse_args()
    config = load_config()
    if not config.openalgo_api_key:
        raise SystemExit("OPENALGO_API_KEY is not configured")
    initialize_database(config.database_path)
    service = SandboxExecutionService(
        config.database_path,
        AuditService(DuckDBAuditRepository(config.database_path)),
        OpenAlgoClient(
            config.openalgo_base_url,
            config.openalgo_api_key,
        ),
    )

    if args.command == "prepare":
        risk = RiskService(config.database_path).evaluate(
            run_id="run_openalgo_operator_workflow",
            signal_id="sig_openalgo_operator_workflow",
            signal_type="entry",
            symbol=args.symbol,
            price=args.reference_price,
            requested_quantity=args.quantity,
            confidence=1.0,
            execution_mode=ExecutionMode.SEMI_AUTO,
        )
        if not risk.approved:
            raise SystemExit(f"Risk rejected the workflow: {risk.reason}")
        result = service.prepare_intent(
            decision_id=risk.decision_id,
            symbol=args.symbol,
            exchange=args.exchange,
            side=args.side,
            product=args.product,
            order_type=args.order_type,
            quantity=risk.approved_quantity,
            strategy_name=args.strategy,
            limit_price=args.limit_price,
            trigger_price=args.trigger_price,
            requested_by=args.actor,
        )
    elif args.command == "approve-and-submit":
        if args.confirm != CONFIRMATION:
            raise SystemExit(
                "Confirmation token did not match. No order was submitted."
            )
        intent = service.get_intent(args.intent_id)
        service.decide(
            intent["approval_id"],
            approved=True,
            decided_by=args.actor,
            reason=args.reason,
        )
        result = service.submit(args.intent_id, actor=args.actor)
    else:
        result = service.reconcile(
            args.intent_id,
            actor=args.actor,
        )

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
