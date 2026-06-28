from __future__ import annotations

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
