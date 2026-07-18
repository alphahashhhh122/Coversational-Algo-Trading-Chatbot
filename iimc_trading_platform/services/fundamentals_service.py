"""Deterministic fundamental analysis over user-imported statements.

No provider fabricates data: statements are imported explicitly (annual or
quarterly), every ratio records its formula and inputs, and missing inputs
produce warnings instead of invented numbers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect

_FIELDS = (
    "revenue", "operating_profit", "net_income", "total_assets",
    "total_equity", "total_debt", "current_assets", "current_liabilities",
    "operating_cash_flow", "capital_expenditure", "shares_outstanding",
    "dividends_paid",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FundamentalsService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def import_statements(
        self,
        *,
        symbol: str,
        currency: str,
        source: str,
        statements: list[dict[str, Any]],
        imported_by: str,
    ) -> dict[str, Any]:
        if not statements:
            raise ValueError("Provide at least one statement period")
        symbol = symbol.upper().strip()
        rows = []
        for item in statements:
            period = str(item.get("period", "")).strip()
            period_end = item.get("period_end")
            if not period or not period_end:
                raise ValueError(
                    "Each statement needs 'period' (e.g. FY2026) and "
                    "'period_end' (YYYY-MM-DD)"
                )
            rows.append([
                f"stmt_{uuid.uuid4().hex[:12]}",
                symbol,
                period,
                period_end,
                currency,
                source,
                *[
                    float(item[field]) if item.get(field) is not None else None
                    for field in _FIELDS
                ],
                imported_by,
                utc_now(),
            ])
        con = connect(self.db_path)
        try:
            for row in rows:
                con.execute(
                    "DELETE FROM financial_statements "
                    "WHERE symbol = ? AND period = ? AND period_end = ?",
                    [row[1], row[2], row[3]],
                )
                con.execute(
                    "INSERT INTO financial_statements VALUES ("
                    + ", ".join(["?"] * (6 + len(_FIELDS) + 2))
                    + ")",
                    row,
                )
        finally:
            con.close()
        return {"symbol": symbol, "imported_periods": len(rows)}

    def list_statements(self, symbol: str) -> list[dict[str, Any]]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT period, period_end, currency, source, "
                + ", ".join(_FIELDS)
                + " FROM financial_statements WHERE symbol = ? "
                "ORDER BY period_end",
                [symbol.upper().strip()],
            ).fetchall()
        finally:
            con.close()
        return [
            {
                "period": row[0],
                "period_end": str(row[1]),
                "currency": row[2],
                "source": row[3],
                **{field: row[4 + i] for i, field in enumerate(_FIELDS)},
            }
            for row in rows
        ]

    def analyze(
        self,
        symbol: str,
        *,
        market_price: float | None = None,
    ) -> dict[str, Any]:
        statements = self.list_statements(symbol)
        if not statements:
            raise ValueError(
                f"No financial statements stored for {symbol.upper()}. "
                "Import them via POST /fundamentals/statements first."
            )
        latest = statements[-1]
        previous = statements[-2] if len(statements) > 1 else None
        ratios: list[dict[str, Any]] = []
        warnings: list[str] = []

        def ratio(name, formula, value, inputs) -> None:
            ratios.append({
                "name": name,
                "formula": formula,
                "value": round(value, 4) if value is not None else None,
                "inputs": inputs,
            })

        def need(item, *fields) -> bool:
            missing = [f for f in fields if item.get(f) is None]
            if missing:
                warnings.append(
                    f"{item['period']}: missing {', '.join(missing)}"
                )
                return False
            return True

        if previous and need(latest, "revenue") and need(previous, "revenue"):
            if previous["revenue"]:
                ratio(
                    "revenue_growth",
                    "(revenue_t - revenue_t-1) / revenue_t-1",
                    (latest["revenue"] - previous["revenue"])
                    / previous["revenue"],
                    {"revenue_t": latest["revenue"],
                     "revenue_t_minus_1": previous["revenue"]},
                )
        if previous and need(latest, "net_income") and need(previous, "net_income"):
            if previous["net_income"]:
                ratio(
                    "earnings_growth",
                    "(net_income_t - net_income_t-1) / net_income_t-1",
                    (latest["net_income"] - previous["net_income"])
                    / abs(previous["net_income"]),
                    {"net_income_t": latest["net_income"],
                     "net_income_t_minus_1": previous["net_income"]},
                )
        if need(latest, "operating_profit", "revenue") and latest["revenue"]:
            ratio(
                "operating_margin",
                "operating_profit / revenue",
                latest["operating_profit"] / latest["revenue"],
                {"operating_profit": latest["operating_profit"],
                 "revenue": latest["revenue"]},
            )
        if need(latest, "net_income", "revenue") and latest["revenue"]:
            ratio(
                "net_margin", "net_income / revenue",
                latest["net_income"] / latest["revenue"],
                {"net_income": latest["net_income"],
                 "revenue": latest["revenue"]},
            )
        if need(latest, "net_income", "total_equity") and latest["total_equity"]:
            ratio(
                "roe", "net_income / total_equity",
                latest["net_income"] / latest["total_equity"],
                {"net_income": latest["net_income"],
                 "total_equity": latest["total_equity"]},
            )
        if need(latest, "net_income", "total_assets") and latest["total_assets"]:
            ratio(
                "roa", "net_income / total_assets",
                latest["net_income"] / latest["total_assets"],
                {"net_income": latest["net_income"],
                 "total_assets": latest["total_assets"]},
            )
        if need(latest, "total_debt", "total_equity") and latest["total_equity"]:
            ratio(
                "debt_to_equity", "total_debt / total_equity",
                latest["total_debt"] / latest["total_equity"],
                {"total_debt": latest["total_debt"],
                 "total_equity": latest["total_equity"]},
            )
        if need(latest, "current_assets", "current_liabilities") and latest["current_liabilities"]:
            ratio(
                "current_ratio", "current_assets / current_liabilities",
                latest["current_assets"] / latest["current_liabilities"],
                {"current_assets": latest["current_assets"],
                 "current_liabilities": latest["current_liabilities"]},
            )
        if need(latest, "operating_cash_flow", "capital_expenditure"):
            fcf = latest["operating_cash_flow"] - latest["capital_expenditure"]
            ratio(
                "free_cash_flow", "operating_cash_flow - capital_expenditure",
                fcf,
                {"operating_cash_flow": latest["operating_cash_flow"],
                 "capital_expenditure": latest["capital_expenditure"]},
            )
        eps = None
        if need(latest, "net_income", "shares_outstanding") and latest["shares_outstanding"]:
            eps = latest["net_income"] / latest["shares_outstanding"]
            ratio(
                "eps", "net_income / shares_outstanding", eps,
                {"net_income": latest["net_income"],
                 "shares_outstanding": latest["shares_outstanding"]},
            )
        if market_price is not None and eps:
            ratio(
                "pe_ratio", "market_price / eps", market_price / eps,
                {"market_price": market_price, "eps": round(eps, 4)},
            )
        elif market_price is None:
            warnings.append(
                "No market price supplied; P/E and market-based ratios "
                "were skipped."
            )
        if need(latest, "dividends_paid", "net_income") and latest["net_income"]:
            ratio(
                "payout_ratio", "dividends_paid / net_income",
                latest["dividends_paid"] / latest["net_income"],
                {"dividends_paid": latest["dividends_paid"],
                 "net_income": latest["net_income"]},
            )
        return {
            "symbol": symbol.upper().strip(),
            "periods_available": [item["period"] for item in statements],
            "latest_period": latest["period"],
            "currency": latest["currency"],
            "source": latest["source"],
            "ratios": ratios,
            "warnings": sorted(set(warnings)),
            "no_synthetic_fallback": True,
        }
