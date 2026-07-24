"""Resolve broker ticker symbols to readable company names.

Reads the OpenAlgo master contract (``openalgo.db`` ``symtoken`` table)
read-only and caches the symbol->name map per exchange. Everything falls
back to the ticker when a name is unavailable, so callers never break and
nothing is fabricated.
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path


def _default_root() -> Path:
    return Path.home() / "openalgo"


# Static fallback for the NIFTY 50 constituents (matching the screener
# universe), used only when the OpenAlgo master contract isn't installed —
# fresh clones and CI still resolve the major names. The master contract
# always takes precedence when present.
_BUILTIN_NSE_NAMES: dict[str, str] = {
    "ADANIENT": "Adani Enterprises",
    "ADANIPORTS": "Adani Ports & SEZ",
    "APOLLOHOSP": "Apollo Hospitals",
    "ASIANPAINT": "Asian Paints",
    "AXISBANK": "Axis Bank",
    "BAJAJ-AUTO": "Bajaj Auto",
    "BAJAJFINSV": "Bajaj Finserv",
    "BAJFINANCE": "Bajaj Finance",
    "BEL": "Bharat Electronics",
    "BHARTIARTL": "Bharti Airtel",
    "BPCL": "Bharat Petroleum",
    "BRITANNIA": "Britannia Industries",
    "CIPLA": "Cipla",
    "COALINDIA": "Coal India",
    "DIVISLAB": "Divi's Laboratories",
    "DRREDDY": "Dr. Reddy's Laboratories",
    "EICHERMOT": "Eicher Motors",
    "GRASIM": "Grasim Industries",
    "HCLTECH": "HCL Technologies",
    "HDFCBANK": "HDFC Bank",
    "HDFCLIFE": "HDFC Life Insurance",
    "HEROMOTOCO": "Hero MotoCorp",
    "HINDALCO": "Hindalco Industries",
    "HINDUNILVR": "Hindustan Unilever",
    "ICICIBANK": "ICICI Bank",
    "INDUSINDBK": "IndusInd Bank",
    "INFY": "Infosys",
    "ITC": "ITC",
    "JSWSTEEL": "JSW Steel",
    "KOTAKBANK": "Kotak Mahindra Bank",
    "LT": "Larsen & Toubro",
    "LTIM": "LTIMindtree",
    "M&M": "Mahindra & Mahindra",
    "MARUTI": "Maruti Suzuki",
    "NESTLEIND": "Nestle India",
    "NTPC": "NTPC",
    "ONGC": "Oil & Natural Gas Corporation",
    "POWERGRID": "Power Grid Corporation",
    "RELIANCE": "Reliance Industries",
    "SBILIFE": "SBI Life Insurance",
    "SBIN": "State Bank of India",
    "SHRIRAMFIN": "Shriram Finance",
    "SUNPHARMA": "Sun Pharmaceutical",
    "TATACONSUM": "Tata Consumer Products",
    "TATAMOTORS": "Tata Motors",
    "TATASTEEL": "Tata Steel",
    "TCS": "Tata Consultancy Services",
    "TECHM": "Tech Mahindra",
    "TITAN": "Titan Company",
    "TRENT": "Trent",
    "ULTRACEMCO": "UltraTech Cement",
    "WIPRO": "Wipro",
}


def _pretty(raw: str) -> str:
    """'RELIANCE INDUSTRIES LTD' -> 'Reliance Industries'."""
    name = " ".join(raw.strip().split()).title()
    for suffix in (" Ltd.", " Ltd", " Limited"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.strip()


@lru_cache(maxsize=16)
def _name_map(root_str: str, exchange: str) -> dict[str, str]:
    database_path = Path(root_str) / "db" / "openalgo.db"
    if not database_path.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT symbol, name FROM symtoken WHERE exchange = ?",
                [exchange.upper()],
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    mapping: dict[str, str] = {}
    for symbol, name in rows:
        if symbol and name and symbol not in mapping:
            mapping[str(symbol).upper()] = _pretty(str(name))
    return mapping


def company_name(
    symbol: str | None,
    exchange: str = "NSE",
    *,
    openalgo_root: Path | None = None,
) -> str | None:
    """Readable company name for a ticker, or None if unknown."""
    if not symbol:
        return None
    root = openalgo_root or _default_root()
    upper_exchange = (exchange or "NSE").upper()
    upper_symbol = str(symbol).upper()
    name = _name_map(str(root), upper_exchange).get(upper_symbol)
    if name:
        return name
    if upper_exchange == "NSE":
        return _BUILTIN_NSE_NAMES.get(upper_symbol)
    return None
