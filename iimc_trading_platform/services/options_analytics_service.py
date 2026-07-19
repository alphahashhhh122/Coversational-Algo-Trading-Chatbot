"""Deterministic option-chain analytics over OpenAlgo chain data.

All figures (ATM strike, put-call ratio, max-OI strikes, straddle cost)
are computed from the broker-provided chain; nothing is estimated or
fabricated when fields are missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..infrastructure.openalgo import OpenAlgoClient


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


class OptionsAnalyticsService:
    def __init__(
        self,
        db_path: Path,
        client: OpenAlgoClient | None = None,
    ) -> None:
        self.db_path = db_path
        self.client = client

    def chain_snapshot(
        self,
        *,
        underlying: str,
        exchange: str = "NSE_INDEX",
        expiry_date: str | None = None,
        strike_count: int = 10,
    ) -> dict[str, Any]:
        if self.client is None:
            raise ValueError(
                "OpenAlgo credentials are required for option-chain data"
            )
        underlying = underlying.upper().strip()
        resolved_expiry = expiry_date
        if not resolved_expiry:
            expiries = self.client.option_expiries(
                symbol=underlying,
                exchange="NFO" if exchange.startswith("NSE") else "BFO",
                instrumenttype="options",
            )
            listed = expiries.get("data") or []
            if isinstance(listed, dict):
                listed = listed.get("expiry_dates") or listed.get("data") or []
            if not listed:
                raise ValueError(
                    f"No option expiries returned for {underlying}"
                )
            first = listed[0]
            resolved_expiry = (
                first.get("expiry") if isinstance(first, dict) else str(first)
            )
        response = self.client.option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=resolved_expiry,
            strike_count=strike_count,
        )
        data = response.get("data") or {}
        rows = (
            data.get("chain")
            or data.get("option_chain")
            or (data if isinstance(data, list) else [])
        )
        if not rows:
            raise ValueError(
                f"OpenAlgo returned an empty option chain for {underlying} "
                f"{resolved_expiry}"
            )
        normalized = []
        total_call_oi = 0.0
        total_put_oi = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            call = row.get("ce") or row.get("call") or {}
            put = row.get("pe") or row.get("put") or {}
            strike = _to_float(row.get("strike") or row.get("strike_price"))
            if strike is None:
                continue
            call_oi = _to_float(call.get("oi") or call.get("open_interest"))
            put_oi = _to_float(put.get("oi") or put.get("open_interest"))
            entry = {
                "strike": strike,
                "call_ltp": _to_float(call.get("ltp")),
                "call_oi": call_oi,
                "put_ltp": _to_float(put.get("ltp")),
                "put_oi": put_oi,
                "call_symbol": call.get("symbol"),
                "put_symbol": put.get("symbol"),
            }
            normalized.append(entry)
            total_call_oi += call_oi or 0.0
            total_put_oi += put_oi or 0.0
        if not normalized:
            raise ValueError("The option chain had no parseable strikes")
        normalized.sort(key=lambda item: item["strike"])
        underlying_ltp = _to_float(
            data.get("underlying_ltp")
            or data.get("spot")
            or data.get("underlying_price")
        )
        atm = (
            min(
                normalized,
                key=lambda item: abs(item["strike"] - underlying_ltp),
            )
            if underlying_ltp is not None
            else None
        )
        max_call = max(
            (item for item in normalized if item["call_oi"] is not None),
            key=lambda item: item["call_oi"],
            default=None,
        )
        max_put = max(
            (item for item in normalized if item["put_oi"] is not None),
            key=lambda item: item["put_oi"],
            default=None,
        )
        straddle_cost = (
            atm["call_ltp"] + atm["put_ltp"]
            if atm
            and atm["call_ltp"] is not None
            and atm["put_ltp"] is not None
            else None
        )
        return {
            "underlying": underlying,
            "exchange": exchange,
            "expiry_date": resolved_expiry,
            "underlying_ltp": underlying_ltp,
            "strike_rows": normalized,
            "analytics": {
                "atm_strike": atm["strike"] if atm else None,
                "atm_call_ltp": atm["call_ltp"] if atm else None,
                "atm_put_ltp": atm["put_ltp"] if atm else None,
                "atm_straddle_cost": straddle_cost,
                "put_call_oi_ratio": (
                    round(total_put_oi / total_call_oi, 4)
                    if total_call_oi
                    else None
                ),
                "max_call_oi_strike": (
                    max_call["strike"] if max_call else None
                ),
                "max_put_oi_strike": max_put["strike"] if max_put else None,
                "total_call_oi": total_call_oi,
                "total_put_oi": total_put_oi,
            },
        }
