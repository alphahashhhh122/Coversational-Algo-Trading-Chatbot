"""Strategies, backtests, runs, and robustness experiments.

One slice of the tool catalogue. ``build`` takes only the services its
own tools use, so each group's dependencies are visible instead of being
shared implicitly through one factory's scope.
"""

from __future__ import annotations

from typing import Any

from ...services.order_service import get_order_timeline
from ...services.risk_service import get_risk_summary
from ..contracts import _require_result
from ..contracts import ToolCapabilityMetadata, ToolDefinition
from ..inputs import (
    UpdateCustomStrategySpecInput,
    CompileCustomStrategyInput,
    CreateCustomStrategySpecInput,
    EmptyInput,
    ExperimentIdInput,
    ListCustomStrategySpecsInput,
    RobustnessExperimentInput,
    RunBacktestInput,
    RunComparisonInput,
    RunCustomStrategySpecInput,
    RunIdInput,
    StrategyOptimizationInput,
    WalkForwardValidationInput,
)


def build(
    *,
    _resolve_dataset_for_symbol: Any,
    backtests: Any,
    custom_strategies: Any,
    db_path: Any,
    evidence: Any,
    optimizer: Any,
    robustness: Any,
    run_backtest_tool: Any,
    submit_robustness_tool: Any,
) -> list[ToolDefinition]:
    return [
                ToolDefinition(
                    name="run_strategy_optimization",
                    description=(
                        "Discover a good strategy configuration for a symbol: "
                        "backtests a small parameter grid for a template "
                        "(ema_crossover/sma_crossover) over stored history and "
                        "returns the ranked leaderboard and the best config by "
                        "historical return, flagging too-few-trade overfits. "
                        "Research backtests only; never trades; reports real "
                        "metrics without fabrication. Use for 'find/optimise a "
                        "strategy for SYMBOL'."
                    ),
                    input_model=StrategyOptimizationInput,
                    handler=lambda value: optimizer.optimize(
                        dataset_id=_resolve_dataset_for_symbol(
                            StrategyOptimizationInput.model_validate(
                                value.model_dump()
                            ).symbol,
                            StrategyOptimizationInput.model_validate(
                                value.model_dump()
                            ).exchange,
                            StrategyOptimizationInput.model_validate(
                                value.model_dump()
                            ).asset_class,
                            StrategyOptimizationInput.model_validate(
                                value.model_dump()
                            ).interval,
                        ),
                        strategy_name=StrategyOptimizationInput.model_validate(
                            value.model_dump()
                        ).strategy_name,
                    ),
                    side_effects="runs several research backtests over stored data",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("backtest", "optimize", "research"),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="validate_strategy_walk_forward",
                    description=(
                        "Out-of-sample check for a symbol's best strategy config: "
                        "optimises a template grid on older data, then tests the "
                        "winner on newer, untouched data and reports whether it holds "
                        "up or is overfit. Research backtests only; never trades; "
                        "reports the real in-sample vs out-of-sample gap without "
                        "fabrication. Use for 'walk-forward / out-of-sample / is that "
                        "strategy robust for SYMBOL'."
                    ),
                    input_model=WalkForwardValidationInput,
                    handler=lambda value: optimizer.walk_forward(
                        dataset_id=_resolve_dataset_for_symbol(
                            WalkForwardValidationInput.model_validate(
                                value.model_dump()
                            ).symbol,
                            WalkForwardValidationInput.model_validate(
                                value.model_dump()
                            ).exchange,
                            WalkForwardValidationInput.model_validate(
                                value.model_dump()
                            ).asset_class,
                            WalkForwardValidationInput.model_validate(
                                value.model_dump()
                            ).interval,
                        ),
                        strategy_name=WalkForwardValidationInput.model_validate(
                            value.model_dump()
                        ).strategy_name,
                    ),
                    side_effects="runs research backtests on train/test splits",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("backtest", "validate", "research"),
                        execution_modes=("research",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="list_strategies",
                    description=(
                        "List registered deterministic strategy plugins and "
                        "parameter schemas."
                    ),
                    input_model=EmptyInput,
                    handler=lambda value: {
                        "strategies": backtests.list_strategies()
                    },
                    side_effects="none",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("list",),
                        execution_modes=("research",),
                        required_data=("strategy_registry",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="get_custom_strategy_capabilities",
                    description=(
                        "Return the current deterministic custom-strategy rule "
                        "vocabulary, position sides, risk controls, and execution "
                        "policy so unsupported requests can be identified before "
                        "a strategy draft is created."
                    ),
                    input_model=EmptyInput,
                    handler=lambda _: custom_strategies.capabilities(),
                    side_effects="read-only capability query",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("inspect_capabilities",),
                        asset_classes=(
                            "equity",
                            "index",
                            "futures",
                            "options",
                            "commodity",
                            "crypto",
                        ),
                        execution_modes=("research",),
                        required_data=("OHLCV",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="compile_custom_strategy_spec",
                    description=(
                        "Compile a plain-language strategy description (EMA/SMA "
                        "crossovers, RSI, MACD, Bollinger Bands, ATR, VWAP, price/"
                        "volume conditions, stop loss, take profit, trailing stop, "
                        "session windows, long/short) into a structured, editable "
                        "rule spec for human review. Read-only: nothing is saved "
                        "or executed, and unparsed clauses are reported verbatim."
                    ),
                    input_model=CompileCustomStrategyInput,
                    handler=lambda value: custom_strategies.compile_from_text(
                        **CompileCustomStrategyInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects="none",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("draft_strategy", "validate_strategy_spec"),
                        asset_classes=(
                            "equity",
                            "index",
                            "futures",
                            "options",
                            "commodity",
                            "crypto",
                        ),
                        execution_modes=("research",),
                        required_data=("strategy_spec",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="create_custom_strategy_spec",
                    description=(
                        "Create and persist a governed draft strategy spec from "
                        "structured indicators, entry rules, exit rules, and risk "
                        "constraints. This does not execute generated code or place "
                        "orders; unsupported primitives are marked requires_review."
                    ),
                    input_model=CreateCustomStrategySpecInput,
                    handler=lambda value: custom_strategies.create_spec(
                        **CreateCustomStrategySpecInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects=(
                        "creates a persisted custom strategy draft for review"
                    ),
                    retry_safe=False,
                    required_role="researcher",
                    capabilities=ToolCapabilityMetadata(
                        actions=("draft_strategy", "validate_strategy_spec"),
                        asset_classes=(
                            "equity",
                            "index",
                            "futures",
                            "options",
                            "commodity",
                            "crypto",
                        ),
                        execution_modes=("research",),
                        required_data=("strategy_spec",),
                        requires_approval=True,
                        risk_level="medium",
                    ),
                ),
                ToolDefinition(
                    name="update_custom_strategy_spec",
                    description=(
                        "Update a persisted custom strategy spec after human "
                        "review/editing and revalidate it. Unsupported primitives "
                        "are marked requires_review; nothing is executed."
                    ),
                    input_model=UpdateCustomStrategySpecInput,
                    handler=lambda value: custom_strategies.update_spec(
                        **UpdateCustomStrategySpecInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects=(
                        "updates a persisted custom strategy draft after review"
                    ),
                    retry_safe=False,
                    required_role="researcher",
                    capabilities=ToolCapabilityMetadata(
                        actions=("draft_strategy", "validate_strategy_spec"),
                        asset_classes=(
                            "equity",
                            "index",
                            "futures",
                            "options",
                            "commodity",
                            "crypto",
                        ),
                        execution_modes=("research",),
                        required_data=("custom_strategy_specs",),
                        requires_approval=True,
                        risk_level="medium",
                    ),
                ),
                ToolDefinition(
                    name="list_custom_strategy_specs",
                    description=(
                        "List governed custom strategy draft specs, including "
                        "whether each spec is executable with current primitives "
                        "or requires human review/new backend implementation."
                    ),
                    input_model=ListCustomStrategySpecsInput,
                    handler=lambda value: custom_strategies.list_specs(
                        ListCustomStrategySpecsInput.model_validate(
                            value.model_dump()
                        ).limit
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("list", "review_strategy_specs"),
                        execution_modes=("research",),
                        required_data=("custom_strategy_specs",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="run_custom_strategy_spec",
                    description=(
                        "Backtest a persisted custom strategy spec through the "
                        "native deterministic rule-spec runtime. Unsupported specs "
                        "fail closed; arbitrary generated code is never executed."
                    ),
                    input_model=RunCustomStrategySpecInput,
                    handler=lambda value: custom_strategies.run_backtest(
                        **RunCustomStrategySpecInput.model_validate(
                            value.model_dump()
                        ).model_dump()
                    ),
                    side_effects=(
                        "creates persisted research workflow records for a custom "
                        "rule-spec strategy"
                    ),
                    retry_safe=False,
                    required_role="researcher",
                    capabilities=ToolCapabilityMetadata(
                        actions=("backtest", "execute_strategy_spec"),
                        asset_classes=(
                            "equity",
                            "index",
                            "futures",
                            "options",
                            "commodity",
                            "crypto",
                        ),
                        execution_modes=("research", "semi_auto", "live"),
                        required_data=(
                            "custom_strategy_specs",
                            "historical_ohlcv",
                        ),
                        risk_level="medium",
                    ),
                ),
                ToolDefinition(
                    name="run_backtest",
                    description=(
                        "Run a deterministic strategy on a governed dataset. "
                        "Stores the run, signals, risk decisions, simulated orders, "
                        "fills, and performance summary."
                    ),
                    input_model=RunBacktestInput,
                    handler=run_backtest_tool,
                    side_effects="creates persisted research workflow records",
                    retry_safe=False,
                    required_role="researcher",
                    capabilities=ToolCapabilityMetadata(
                        actions=("backtest",),
                        asset_classes=(
                            "equity",
                            "index",
                            "futures",
                            "options",
                            "commodity",
                            "crypto",
                        ),
                        execution_modes=("research", "semi_auto", "live"),
                        required_data=("historical_ohlcv", "strategy_registry"),
                        risk_level="medium",
                    ),
                ),
                ToolDefinition(
                    name="get_backtest_result",
                    description="Retrieve a stored strategy run and summary by run ID.",
                    input_model=RunIdInput,
                    handler=lambda value: _require_result(
                        backtests.get_result(
                            RunIdInput.model_validate(
                                value.model_dump()
                            ).run_id
                        )
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                    capabilities=ToolCapabilityMetadata(
                        actions=("monitor",),
                        execution_modes=("research",),
                        required_data=("performance_summaries",),
                        risk_level="low",
                    ),
                ),
                ToolDefinition(
                    name="get_performance",
                    description=(
                        "Retrieve performance summary, equity curve, and drawdown "
                        "for a stored run."
                    ),
                    input_model=RunIdInput,
                    handler=lambda value: backtests.get_performance(
                        RunIdInput.model_validate(value.model_dump()).run_id
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="get_risk_decisions",
                    description="Retrieve detailed risk decisions for a stored run.",
                    input_model=RunIdInput,
                    handler=lambda value: get_risk_summary(
                        db_path,
                        RunIdInput.model_validate(value.model_dump()).run_id,
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="get_order_timeline",
                    description=(
                        "Retrieve orders and append-only state transitions for a run."
                    ),
                    input_model=RunIdInput,
                    handler=lambda value: get_order_timeline(
                        db_path,
                        RunIdInput.model_validate(value.model_dump()).run_id,
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="get_run_timeline",
                    description=(
                        "Retrieve one chronological workflow joining strategy "
                        "signals, risk decisions, orders, and fills for a run."
                    ),
                    input_model=RunIdInput,
                    handler=lambda value: evidence.run_timeline(
                        RunIdInput.model_validate(value.model_dump()).run_id
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="compare_runs",
                    description=(
                        "Compare two to ten stored strategy runs using persisted "
                        "performance evidence."
                    ),
                    input_model=RunComparisonInput,
                    handler=lambda value: evidence.compare_runs(
                        RunComparisonInput.model_validate(
                            value.model_dump()
                        ).run_ids
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="create_run_report",
                    description=(
                        "Generate and persist a Markdown evidence report for one "
                        "stored strategy run."
                    ),
                    input_model=RunIdInput,
                    handler=lambda value: evidence.create_run_report(
                        RunIdInput.model_validate(value.model_dump()).run_id,
                        created_by="chat_user",
                    ),
                    side_effects="creates a persisted report artifact",
                    retry_safe=False,
                    required_role="researcher",
                ),
                ToolDefinition(
                    name="list_reports",
                    description="List persisted strategy evidence reports.",
                    input_model=EmptyInput,
                    handler=lambda value: evidence.list_reports(),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="run_robustness_experiment",
                    description=(
                        "Queue a durable chronological train/test parameter "
                        "sensitivity and benchmark experiment."
                    ),
                    input_model=RobustnessExperimentInput,
                    handler=submit_robustness_tool,
                    side_effects="creates a durable queued work task",
                    retry_safe=True,
                    required_role="researcher",
                ),
                ToolDefinition(
                    name="get_robustness_experiment",
                    description=(
                        "Retrieve a persisted robustness experiment, trials, "
                        "benchmark, and verdict."
                    ),
                    input_model=ExperimentIdInput,
                    handler=lambda value: robustness.get(
                        ExperimentIdInput.model_validate(
                            value.model_dump()
                        ).experiment_id
                    ),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
                ToolDefinition(
                    name="list_robustness_experiments",
                    description="List persisted strategy robustness experiments.",
                    input_model=EmptyInput,
                    handler=lambda value: robustness.list(),
                    side_effects="read-only database query",
                    retry_safe=True,
                ),
    ]
