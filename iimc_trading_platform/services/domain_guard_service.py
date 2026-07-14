from __future__ import annotations

from dataclasses import dataclass


IN_SCOPE_TERMS = {
    "trading",
    "market",
    "stock",
    "nifty",
    "reliance",
    "tcs",
    "openalgo",
    "dhan",
    "backtest",
    "strategy",
    "risk",
    "order",
    "portfolio",
    "news",
    "dataset",
    "rag",
    "architecture",
    "platform",
}


@dataclass(frozen=True)
class DomainGuardResult:
    allowed: bool
    reason: str
    risk_level: str


class DomainGuardService:
    def check(self, message: str) -> DomainGuardResult:
        text = message.lower()
        if any(term in text for term in IN_SCOPE_TERMS):
            return DomainGuardResult(
                allowed=True,
                reason="Prompt is in scope for trading research/platform support.",
                risk_level="normal",
            )
        return DomainGuardResult(
            allowed=False,
            reason=(
                "Prompt is outside the trading research, platform, data, "
                "OpenAlgo, or operator-workflow scope."
            ),
            risk_level="blocked",
        )
