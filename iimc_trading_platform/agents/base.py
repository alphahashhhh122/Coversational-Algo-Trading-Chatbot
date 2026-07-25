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
    """Caps for a single run. Exceeding a cap yields a 'partial' result."""

    max_seconds: float = 120.0
    max_steps: int = 25
    max_llm_calls: int = 10


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
        try:
            payload = self.runner(task)
        except Exception as exc:  # noqa: BLE001 - captured honestly, not raised
            elapsed = round(time.monotonic() - started, 3)
            return AgentResult(
                status="failed",
                gaps=[str(exc)[:300]],
                cost={"seconds": elapsed},
            )
        elapsed = round(time.monotonic() - started, 3)
        findings, evidence, gaps = self.interpret(payload)
        status = "ok" if not gaps else "partial"
        if elapsed > task.budget.max_seconds:
            status = "partial"
            gaps = [*gaps, f"budget: run took {elapsed}s (cap {task.budget.max_seconds}s)"]
        return AgentResult(
            status=status,
            findings=findings,
            evidence=evidence,
            gaps=gaps,
            cost={"seconds": elapsed},
        )
