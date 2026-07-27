"""The Agent contract (docs/ATL_TRANSITION.md §4.1).

Every registered agent implements the same small interface: it receives an
``AgentTask`` and returns an ``AgentResult`` carrying structured findings, the
evidence needed to score the run, and an honest list of gaps. Adapters wrap the
existing services so nothing is rewritten — the contract is a boundary, not a
framework.

Safety note: the contract has no order-placement surface. Agents research,
prepare, and notify; order approval lives outside the kernel, with a human.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

_CATEGORIES = ("research", "strategy", "monitor", "assistant")


@dataclass(frozen=True)
class AgentBudget:
    """Caps for a single run. Exceeding a cap yields a 'partial' result.

    Budgets exist so an autonomous, scheduled agent cannot run away with time
    or LLM spend. Exceeding a cap is reported honestly (``partial`` + a gap
    naming the cap) rather than silently truncating the work, so a scheduled
    run that hit a wall is distinguishable from one that genuinely finished.
    """

    max_seconds: float = 120.0
    max_steps: int = 25
    max_llm_calls: int = 10


class BudgetLedger:
    """Tracks consumption against a budget during a run."""

    def __init__(self, budget: AgentBudget) -> None:
        self.budget = budget
        self.steps = 0
        self.llm_calls = 0

    def step(self, count: int = 1) -> None:
        self.steps += count

    def llm_call(self, count: int = 1) -> None:
        self.llm_calls += count

    def exceeded(self, elapsed: float) -> list[str]:
        """Every cap this run blew through, named plainly."""
        breaches: list[str] = []
        if elapsed > self.budget.max_seconds:
            breaches.append(
                f"budget: took {elapsed}s (cap {self.budget.max_seconds}s)"
            )
        if self.steps > self.budget.max_steps:
            breaches.append(
                f"budget: {self.steps} steps (cap {self.budget.max_steps})"
            )
        if self.llm_calls > self.budget.max_llm_calls:
            breaches.append(
                f"budget: {self.llm_calls} LLM calls "
                f"(cap {self.budget.max_llm_calls})"
            )
        return breaches


@dataclass(frozen=True)
class AgentTask:
    """What an agent is asked to do."""

    task_type: str
    symbol: str | None = None
    symbols: tuple[str, ...] = ()
    exchange: str = "NSE"
    params: dict[str, Any] = field(default_factory=dict)
    budget: AgentBudget = field(default_factory=AgentBudget)


@dataclass
class AgentResult:
    """What every run must return. Unavailable data goes in ``gaps``."""

    status: str  # "ok" | "partial" | "failed"
    findings: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Agent(Protocol):
    agent_id: str
    name: str
    version: str
    category: str
    description: str
    capabilities: tuple[str, ...]

    def run(self, task: AgentTask) -> AgentResult: ...


@dataclass
class ServiceAgent:
    """Adapter that turns an existing service call into a contract-compliant
    agent.

    ``runner`` maps an AgentTask to the raw service payload; ``interpret`` maps
    that payload to (findings, evidence, gaps). The kernel wrapper adds timing,
    budget bookkeeping, and honest failure capture, so adapters stay thin.
    """

    agent_id: str
    name: str
    version: str
    category: str
    description: str
    capabilities: tuple[str, ...]
    runner: Callable[[AgentTask], dict[str, Any]]
    interpret: Callable[[dict[str, Any]], tuple[dict[str, Any], list[dict[str, Any]], list[str]]]

    def __post_init__(self) -> None:
        if self.category not in _CATEGORIES:
            raise ValueError(f"category must be one of {_CATEGORIES}")

    def run(self, task: AgentTask) -> AgentResult:
        started = time.monotonic()
        ledger = BudgetLedger(task.budget)
        ledger.step()  # the tool invocation itself
        try:
            payload = self.runner(task)
        except Exception as exc:  # noqa: BLE001 - captured honestly, not raised
            elapsed = round(time.monotonic() - started, 3)
            return AgentResult(
                status="failed",
                gaps=[str(exc)[:300]],
                cost={"seconds": elapsed, "steps": ledger.steps},
            )
        elapsed = round(time.monotonic() - started, 3)
        findings, evidence, gaps = self.interpret(payload)
        breaches = ledger.exceeded(elapsed)
        status = "ok" if not gaps and not breaches else "partial"
        return AgentResult(
            status=status,
            findings=findings,
            evidence=evidence,
            gaps=[*gaps, *breaches],
            cost={
                "seconds": elapsed,
                "steps": ledger.steps,
                "llm_calls": ledger.llm_calls,
            },
        )
