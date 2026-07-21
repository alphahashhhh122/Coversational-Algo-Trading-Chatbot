from __future__ import annotations

import re
import sqlite3
from difflib import SequenceMatcher
from typing import Any

from ..config import AppConfig
from ..infrastructure.openalgo import (
    OpenAlgoAuthenticationError,
    OpenAlgoClient,
    OpenAlgoError,
    OpenAlgoResponseError,
    OpenAlgoUnavailableError,
)


class InstrumentDiscoveryService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def validate_symbol(
        self,
        *,
        symbol: str,
        exchange: str,
    ) -> dict[str, Any]:
        if not self.config.openalgo_api_key:
            return self._credential_required(symbol=symbol, exchange=exchange)
        try:
            response = self._client().symbol_details(
                symbol=symbol.upper(),
                exchange=exchange.upper(),
            )
            data = _public_instrument(response["data"])
            return {
                **self._base(symbol=symbol, exchange=exchange),
                "ok": True,
                "status": "exact_symbol",
                "safe_failure": False,
                "message": "OpenAlgo validated the exact trading symbol.",
                "instrument": data,
                "resolved_symbol": data.get("symbol", symbol.upper()),
                "resolved_exchange": data.get("exchange", exchange.upper()),
                "no_synthetic_fallback": True,
            }
        except OpenAlgoAuthenticationError as exc:
            return self._failed("authentication_failed", str(exc), symbol, exchange)
        except OpenAlgoUnavailableError as exc:
            return self._failed("unavailable", str(exc), symbol, exchange)
        except (OpenAlgoResponseError, OpenAlgoError) as exc:
            return self._failed("provider_error", str(exc), symbol, exchange)

    def search(
        self,
        *,
        query: str,
        exchange: str,
    ) -> dict[str, Any]:
        if not self.config.openalgo_api_key:
            return self._credential_required(symbol=query, exchange=exchange)
        try:
            response = self._client().search_symbols(
                query=query.upper(),
                exchange=exchange.upper(),
            )
            matches = [_public_instrument(item) for item in response["data"]]
            return {
                **self._base(symbol=query, exchange=exchange),
                "ok": True,
                "status": "matches_found" if matches else "no_matches",
                "safe_failure": False,
                "message": (
                    f"OpenAlgo returned {len(matches)} matching instrument(s)."
                ),
                "matches": matches,
                "match_count": len(matches),
                "no_synthetic_fallback": True,
            }
        except OpenAlgoAuthenticationError as exc:
            return self._failed("authentication_failed", str(exc), query, exchange)
        except OpenAlgoUnavailableError as exc:
            return self._failed("unavailable", str(exc), query, exchange)
        except (OpenAlgoResponseError, OpenAlgoError) as exc:
            return self._failed("provider_error", str(exc), query, exchange)

    def quote(
        self,
        *,
        query: str,
        exchange: str,
    ) -> dict[str, Any]:
        """Resolve a provider contract, then obtain its current quote."""
        if not self.config.openalgo_api_key:
            return self._credential_required(symbol=query, exchange=exchange)
        try:
            instrument = None
            for candidate in _instrument_search_candidates(query):
                response = self._client().search_symbols(
                    query=candidate.upper(),
                    exchange=exchange.upper(),
                )
                matches = [
                    _public_instrument(item)
                    for item in response["data"]
                    if isinstance(item, dict)
                ]
                instrument = _best_instrument_match(matches, candidate)
                if instrument is not None:
                    break
            instrument_resolution = "provider_search"
            if instrument is None:
                instrument = self._local_contract_match(
                    query=query,
                    exchange=exchange,
                )
                instrument_resolution = "local_contract_fuzzy_match"
            if instrument is None:
                return {
                    **self._base(symbol=query, exchange=exchange),
                    "ok": False,
                    "status": "no_matches",
                    "safe_failure": True,
                    "message": (
                        f"I couldn't find a stock called \"{query}\" on "
                        f"{exchange.upper()}. Please check the name or the "
                        f"exchange and try again."
                    ),
                    "matches": [],
                    "no_synthetic_fallback": True,
                }
            symbol = str(instrument.get("symbol") or "").upper()
            if not symbol:
                return self._failed(
                    "provider_error",
                    "OpenAlgo instrument search omitted the contract symbol.",
                    query,
                    exchange,
                )
            quote = self._client().quote(
                symbol=symbol,
                exchange=exchange.upper(),
            )
            return {
                **self._base(symbol=query, exchange=exchange),
                "ok": True,
                "status": "quoted",
                "safe_failure": False,
                "message": "OpenAlgo returned a current provider quote.",
                "instrument": instrument,
                "instrument_resolution": instrument_resolution,
                "resolved_symbol": symbol,
                "resolved_exchange": exchange.upper(),
                "quote": _public_quote(quote["data"]),
                "no_synthetic_fallback": True,
            }
        except OpenAlgoAuthenticationError as exc:
            return self._failed("authentication_failed", str(exc), query, exchange)
        except OpenAlgoUnavailableError as exc:
            return self._failed("unavailable", str(exc), query, exchange)
        except (OpenAlgoResponseError, OpenAlgoError) as exc:
            return self._failed("provider_error", str(exc), query, exchange)

    def resolve_option_symbol(
        self,
        *,
        underlying: str,
        exchange: str,
        expiry_date: str,
        offset: str,
        option_type: str,
    ) -> dict[str, Any]:
        if not self.config.openalgo_api_key:
            return self._credential_required(
                symbol=underlying,
                exchange=exchange,
            )
        mapped_exchange = _option_underlying_exchange(exchange)
        try:
            response = self._client().option_symbol(
                underlying=underlying.upper(),
                exchange=mapped_exchange,
                expiry_date=expiry_date.upper(),
                offset=offset.upper(),
                option_type=option_type.upper(),
            )
            return {
                **self._base(symbol=underlying, exchange=exchange),
                "ok": True,
                "status": "resolved",
                "safe_failure": False,
                "message": "OpenAlgo resolved the option contract symbol.",
                "underlying": underlying.upper(),
                "underlying_exchange": mapped_exchange,
                "expiry_date": expiry_date.upper(),
                "offset": offset.upper(),
                "option_type": option_type.upper(),
                "resolved_symbol": response["symbol"],
                "resolved_exchange": response["exchange"],
                "lotsize": response.get("lotsize"),
                "tick_size": response.get("tick_size"),
                "freeze_qty": response.get("freeze_qty"),
                "underlying_ltp": response.get("underlying_ltp"),
                "no_synthetic_fallback": True,
            }
        except OpenAlgoAuthenticationError as exc:
            return self._failed("authentication_failed", str(exc), underlying, exchange)
        except OpenAlgoUnavailableError as exc:
            return self._failed("unavailable", str(exc), underlying, exchange)
        except (OpenAlgoResponseError, OpenAlgoError, ValueError) as exc:
            return self._failed("provider_error", str(exc), underlying, exchange)

    def _client(self) -> OpenAlgoClient:
        return OpenAlgoClient(
            self.config.openalgo_base_url,
            self.config.openalgo_api_key or "",
        )

    def _local_contract_match(
        self,
        *,
        query: str,
        exchange: str,
    ) -> dict[str, Any] | None:
        """Use the downloaded OpenAlgo master contract only after search misses."""
        database_path = self.config.openalgo_root / "db" / "openalgo.db"
        if not database_path.is_file():
            return None
        try:
            uri = f"file:{database_path.as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            try:
                rows = con.execute(
                    """
                    SELECT symbol, brsymbol, name, exchange, brexchange,
                           instrumenttype, expiry, strike, lotsize, tick_size
                    FROM symtoken
                    WHERE exchange = ?
                    """,
                    [exchange.upper()],
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return None
        candidates = [
            {
                "symbol": row[0],
                "brsymbol": row[1],
                "name": row[2],
                "exchange": row[3],
                "brexchange": row[4],
                "instrumenttype": row[5],
                "expiry": row[6],
                "strike": row[7],
                "lotsize": row[8],
                "tick_size": row[9],
            }
            for row in rows
        ]
        return _best_fuzzy_contract_match(candidates, query)

    def _base(self, *, symbol: str, exchange: str) -> dict[str, Any]:
        return {
            "provider": "openalgo",
            "provider_configured": bool(self.config.openalgo_api_key),
            "credentials_redacted": True,
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
        }

    def _credential_required(
        self,
        *,
        symbol: str,
        exchange: str,
    ) -> dict[str, Any]:
        return {
            **self._base(symbol=symbol, exchange=exchange),
            "ok": False,
            "status": "credential_required",
            "safe_failure": True,
            "message": "OPENALGO_API_KEY is not configured.",
            "no_synthetic_fallback": True,
        }

    def _failed(
        self,
        status: str,
        message: str,
        symbol: str,
        exchange: str,
    ) -> dict[str, Any]:
        return {
            **self._base(symbol=symbol, exchange=exchange),
            "ok": False,
            "status": status,
            "safe_failure": True,
            "message": message,
            "no_synthetic_fallback": True,
        }


def _public_instrument(value: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "symbol",
        "brsymbol",
        "name",
        "exchange",
        "brexchange",
        "instrumenttype",
        "expiry",
        "strike",
        "lotsize",
        "tick_size",
        "freeze_qty",
        "token",
    )
    return {key: value.get(key) for key in allowed if key in value}


def _best_instrument_match(
    matches: list[dict[str, Any]],
    query: str,
) -> dict[str, Any] | None:
    if not matches:
        return None
    normalized_query = _normalized_identifier(query)
    if not normalized_query:
        return None
    # 1. Exact match on symbol/brsymbol/name.
    for match in matches:
        values = (
            match.get("symbol"),
            match.get("brsymbol"),
            match.get("name"),
        )
        if any(
            _normalized_identifier(str(value)) == normalized_query
            for value in values
            if value
        ):
            return match
    # 2. Prefix relationship either direction on symbol or name, so
    #    "colgate" -> COLPAL ("COLGATE-PALMOLIVE..."), "infosys" -> INFY, etc.
    for match in matches:
        for value in (match.get("symbol"), match.get("brsymbol"), match.get("name")):
            token = _normalized_identifier(str(value or ""))
            if not token:
                continue
            if token.startswith(normalized_query) or normalized_query.startswith(token):
                return match
    # 3. Strong fuzzy match only. Never return an arbitrary/unrelated
    #    instrument — a wrong-company quote is worse than an honest miss.
    best_score = 0.0
    best_match: dict[str, Any] | None = None
    for match in matches:
        for value in (match.get("symbol"), match.get("brsymbol"), match.get("name")):
            for term in _contract_search_terms(str(value or "")):
                if not term:
                    continue
                score = SequenceMatcher(None, normalized_query, term).ratio()
                if score > best_score:
                    best_score = score
                    best_match = match
    if best_score >= 0.8:
        return best_match
    return None


def _normalized_identifier(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _instrument_search_candidates(query: str) -> list[str]:
    candidates = [query]
    candidates.extend(
        match.group(0)
        for match in re.finditer(r"[A-Za-z0-9]+", query)
        if len(match.group(0)) >= 2
    )
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _best_fuzzy_contract_match(
    contracts: list[dict[str, Any]],
    query: str,
) -> dict[str, Any] | None:
    normalized_query = _normalized_identifier(query)
    if len(normalized_query) < 4:
        return None
    best_score = 0.0
    best_contract: dict[str, Any] | None = None
    for contract in contracts:
        values = (
            str(contract.get("symbol") or ""),
            str(contract.get("brsymbol") or ""),
            str(contract.get("name") or ""),
        )
        score = max(
            (
                SequenceMatcher(None, normalized_query, candidate).ratio()
                for value in values
                for candidate in _contract_search_terms(value)
            ),
            default=0.0,
        )
        if score > best_score:
            best_score = score
            best_contract = contract
    if best_score < 0.84 or best_contract is None:
        return None
    return _public_instrument(best_contract)


def _contract_search_terms(value: str) -> list[str]:
    normalized = _normalized_identifier(value)
    words = [
        _normalized_identifier(word)
        for word in re.findall(r"[A-Za-z0-9]+", value)
    ]
    return [term for term in [normalized, *words] if term]


def _public_quote(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OpenAlgoResponseError("OpenAlgo quote data was not an object")
    allowed = (
        "ltp",
        "last_price",
        "close",
        "open",
        "high",
        "low",
        "volume",
        "oi",
        "bid",
        "ask",
        "change",
        "change_percent",
        "timestamp",
    )
    return {key: value[key] for key in allowed if key in value}


def _option_underlying_exchange(exchange: str) -> str:
    normalized = exchange.upper()
    if normalized in {"NSE_INDEX", "BSE_INDEX"}:
        return normalized
    if normalized == "NFO":
        return "NSE_INDEX"
    if normalized == "BFO":
        return "BSE_INDEX"
    raise ValueError(
        "Option symbol resolution requires NFO/BFO or NSE_INDEX/BSE_INDEX"
    )
