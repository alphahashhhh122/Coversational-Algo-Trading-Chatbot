"""Portfolio-level research: how a basket behaves, not just each name alone.

Every other agent looks at one symbol. This one looks at the relationships
*between* symbols — which is where most portfolio risk actually lives. Two
stocks that each look fine can be the same bet twice over.

It reports:

- **Correlation** between the candidates, computed on timestamp-aligned
  returns (aligning matters: comparing unaligned series silently correlates
  different moments and produces confident nonsense).
- **Concentration** — how much of the risk sits in the largest position, via a
  Herfindahl index over the proposed weights.
- **Weights** under two transparent schemes: equal weight, and
  inverse-volatility (calmer names get more, which is a rule you can inspect
  rather than an opaque optimiser).

**Research output only.** It proposes weights; it places nothing, and there is
no broker path here. Sizing a real position remains a human decision.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

# Below this many overlapping bars, a correlation is noise dressed as a number.
_MIN_OVERLAP = 30


class PortfolioAgentService:
    def __init__(
        self,
        db_path: Path,
        backtest_service: Any,
        dataset_resolver: Callable[[str, str], str | None],
    ) -> None:
        self.db_path = db_path
        self.backtest_service = backtest_service
        self.dataset_resolver = dataset_resolver

    def analyse(
        self,
        symbols: list[str],
        *,
        exchange: str = "NSE",
        scheme: str = "inverse_volatility",
    ) -> dict[str, Any]:
        clean: list[str] = []
        for symbol in symbols:
            up = (symbol or "").upper().strip()
            if up and up not in clean:
                clean.append(up)
        if len(clean) < 2:
            raise ValueError(
                "Give me at least two symbols, e.g. "
                "'build a portfolio from RELIANCE and TATASTEEL'."
            )

        returns: dict[str, dict[Any, float]] = {}
        gaps: list[str] = []
        for symbol in clean:
            series, reason = self._returns_for(symbol, exchange)
            if series is None:
                gaps.append(f"{symbol}: {reason}")
            else:
                returns[symbol] = series

        usable = sorted(returns)
        if len(usable) < 2:
            return {
                "symbols": clean,
                "usable_symbols": usable,
                "status": "insufficient_data",
                "correlations": [],
                "weights": {},
                "gaps": gaps
                or ["not enough symbols with stored history to compare"],
                "no_synthetic_fallback": True,
            }

        correlations = self._correlations(returns, usable, gaps)
        volatility = {
            symbol: _stdev(list(returns[symbol].values())) for symbol in usable
        }
        weights = _weights(usable, volatility, scheme)
        concentration = sum(w * w for w in weights.values())
        avg_corr = (
            sum(row["correlation"] for row in correlations) / len(correlations)
            if correlations
            else None
        )
        return {
            "symbols": clean,
            "usable_symbols": usable,
            "status": "ok",
            "scheme": scheme,
            "weights": weights,
            "volatility": {s: round(v, 8) for s, v in volatility.items()},
            "correlations": correlations,
            "average_correlation": (
                round(avg_corr, 4) if avg_corr is not None else None
            ),
            "concentration_hhi": round(concentration, 4),
            "diversification": _diversification_note(avg_corr, len(usable)),
            "gaps": gaps,
            "not_investment_advice": True,
            "no_synthetic_fallback": True,
        }

    # -- internals ------------------------------------------------------------

    def _returns_for(
        self, symbol: str, exchange: str
    ) -> tuple[dict[Any, float] | None, str]:
        dataset_id = self.dataset_resolver(symbol, exchange)
        if not dataset_id:
            return None, "no stored price history"
        try:
            _ds, candles = self.backtest_service.load_dataset_candles(dataset_id)
        except Exception as exc:  # noqa: BLE001 - reported, not fabricated
            return None, str(exc)[:120]
        series: dict[Any, float] = {}
        previous: float | None = None
        for candle in candles:
            try:
                close = float(candle["close"])
            except (KeyError, TypeError, ValueError):
                continue
            if previous is not None and previous:
                series[candle.get("timestamp")] = (close - previous) / previous
            previous = close
        if len(series) < _MIN_OVERLAP:
            return None, f"only {len(series)} return observations"
        return series, ""

    def _correlations(
        self,
        returns: dict[str, dict[Any, float]],
        usable: list[str],
        gaps: list[str],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for i, left in enumerate(usable):
            for right in usable[i + 1 :]:
                # Align on shared timestamps — correlating unaligned series
                # compares different moments and yields confident nonsense.
                shared = sorted(
                    set(returns[left]) & set(returns[right]),
                    key=lambda ts: str(ts),
                )
                if len(shared) < _MIN_OVERLAP:
                    gaps.append(
                        f"{left}/{right}: only {len(shared)} overlapping bars, "
                        "too few to correlate"
                    )
                    continue
                a = [returns[left][ts] for ts in shared]
                b = [returns[right][ts] for ts in shared]
                corr = _pearson(a, b)
                if corr is None:
                    gaps.append(f"{left}/{right}: no variation to correlate")
                    continue
                rows.append(
                    {
                        "pair": [left, right],
                        "correlation": round(corr, 4),
                        "observations": len(shared),
                        "reading": _corr_reading(corr),
                    }
                )
        return rows


def _weights(
    symbols: list[str], volatility: dict[str, float], scheme: str
) -> dict[str, float]:
    if scheme == "equal_weight" or not any(volatility.values()):
        share = 1 / len(symbols)
        return {s: round(share, 6) for s in symbols}
    # Inverse volatility: calmer names carry more. An explicit, inspectable
    # rule rather than an opaque optimiser.
    #
    # Driven by ``symbols``, not by the volatility map. Iterating the map meant
    # any requested symbol missing from it disappeared from the result — the
    # weights still summed to 1, so a two-name portfolio came back looking
    # complete when three were asked for. A symbol whose volatility cannot be
    # measured gets a zero weight instead: still excluded, but visibly so.
    inverse = {s: (1 / v if (v := volatility.get(s, 0.0)) else 0.0) for s in symbols}
    total = sum(inverse.values())
    if not total:
        share = 1 / len(symbols)
        return {s: round(share, 6) for s in symbols}
    return {s: round(value / total, 6) for s, value in inverse.items()}


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 2:
        return None
    mean_a, mean_b = sum(a) / n, sum(b) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    den_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    den_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if not den_a or not den_b:
        return None
    return num / (den_a * den_b)


def _corr_reading(corr: float) -> str:
    magnitude = abs(corr)
    if magnitude >= 0.8:
        base = "very high"
    elif magnitude >= 0.6:
        base = "high"
    elif magnitude >= 0.3:
        base = "moderate"
    else:
        base = "low"
    return f"{base} {'positive' if corr >= 0 else 'negative'} correlation"


def _diversification_note(avg_corr: float | None, count: int) -> str:
    if avg_corr is None:
        return "not enough overlapping data to judge diversification"
    if avg_corr >= 0.8:
        return (
            f"These {count} names move almost together — holding all of them "
            "is close to holding one position in larger size."
        )
    if avg_corr >= 0.5:
        return (
            f"These {count} names are meaningfully correlated; the basket "
            "diversifies less than the number of positions suggests."
        )
    if avg_corr >= 0.2:
        return f"These {count} names are mildly correlated."
    return f"These {count} names move largely independently."
