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
from .instrument_discovery_service import InstrumentDiscoveryService


SNAPSHOT_TYPES = ("funds", "orderbook", "tradebook", "positionbook")


class OpenAlgoReadinessService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def monitor(self) -> dict[str, Any]:
        base = self._base_payload()
        if not self.config.openalgo_api_key:
            return {
                **base,
                "ok": False,
                "status": "credential_required",
                "safe_failure": True,
                "message": "OPENALGO_API_KEY is not configured.",
                "no_synthetic_fallback": True,
                "checks": self._empty_checks("credential_required"),
            }

        client = self._client()
        checks: dict[str, Any] = {}
        try:
            analyzer = client.analyzer_status()
        except OpenAlgoAuthenticationError as exc:
            return self._failed("authentication_failed", str(exc), checks)
        except OpenAlgoUnavailableError as exc:
            return self._failed("unavailable", str(exc), checks)
        except (OpenAlgoResponseError, OpenAlgoError) as exc:
            return self._failed("provider_error", str(exc), checks)

        checks["analyzer"] = {
            "ok": True,
            "status": "available",
            "data": analyzer,
        }
        for snapshot_type in SNAPSHOT_TYPES:
            try:
                response = client.account_snapshot(snapshot_type)
            except OpenAlgoAuthenticationError as exc:
                checks[snapshot_type] = self._check_failed(
                    "authentication_failed",
                    str(exc),
                )
            except OpenAlgoUnavailableError as exc:
                checks[snapshot_type] = self._check_failed("unavailable", str(exc))
            except (OpenAlgoResponseError, OpenAlgoError) as exc:
                checks[snapshot_type] = self._check_failed(
                    "provider_error",
                    str(exc),
                )
            else:
                checks[snapshot_type] = {
                    "ok": True,
                    "status": "available",
                    "data": response.get("data"),
                }

        analyzer_mode = (
            analyzer.get("analyze_mode") is True
            and analyzer.get("mode") == "analyze"
        )
        failed_checks = [
            name for name, check in checks.items()
            if not check.get("ok")
        ]
        if failed_checks:
            return {
                **base,
                "ok": False,
                "status": "degraded",
                "safe_failure": True,
                "message": (
                    "OpenAlgo is reachable, but these account checks failed: "
                    + ", ".join(failed_checks)
                ),
                "no_synthetic_fallback": True,
                "analyzer_mode": analyzer_mode,
                "live_mode": not analyzer_mode,
                "live_trading_enabled": self.config.allow_live_trading,
                "checks": {**self._empty_checks("not_checked"), **checks},
            }

        return {
            **base,
            "ok": True,
            "status": "available",
            "safe_failure": False,
            "message": "OpenAlgo is reachable with configured credentials.",
            "no_synthetic_fallback": True,
            "analyzer_mode": analyzer_mode,
            "live_mode": not analyzer_mode,
            "live_trading_enabled": self.config.allow_live_trading,
            "checks": checks,
        }

    def readiness(
        self,
        *,
        symbol: str,
        exchange: str,
        asset_class: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        monitor = self.monitor()
        symbol_resolution = self._symbol_resolution(
            symbol=symbol,
            exchange=exchange,
            enabled=monitor["ok"],
        )
        market_enabled = monitor["ok"] and symbol_resolution["ok"]
        resolved_symbol = symbol_resolution.get("resolved_symbol", symbol)
        resolved_exchange = symbol_resolution.get("resolved_exchange", exchange)
        disabled_message = (
            "OpenAlgo account readiness failed first."
            if not monitor["ok"]
            else symbol_resolution.get(
                "message",
                "OpenAlgo could not validate the requested symbol.",
            )
        )
        quote_check = self._market_check(
            "quote",
            symbol=resolved_symbol,
            exchange=resolved_exchange,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            enabled=market_enabled,
            disabled_message=disabled_message,
        )
        history_check = self._market_check(
            "history",
            symbol=resolved_symbol,
            exchange=resolved_exchange,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            enabled=market_enabled,
            disabled_message=disabled_message,
        )
        return {
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
            "resolved_symbol": (
                resolved_symbol.upper()
                if isinstance(resolved_symbol, str)
                else resolved_symbol
            ),
            "resolved_exchange": (
                resolved_exchange.upper()
                if isinstance(resolved_exchange, str)
                else resolved_exchange
            ),
            "asset_class": asset_class.lower(),
            "interval": interval,
            "start_date": start_date,
            "end_date": end_date,
            "provider": "openalgo",
            "provider_configured": bool(self.config.openalgo_api_key),
            "verified_now": monitor["ok"],
            "symbol_resolution": symbol_resolution,
            "quote_available": quote_check["ok"],
            "historical_available": history_check["ok"],
            "quote_status": quote_check,
            "historical_status": history_check,
            "analyzer_path_status": monitor["status"],
            "paper_path_status": (
                "analyzer_available" if monitor["ok"] else "not_configured"
            ),
            "live_path_status": (
                "disabled"
                if not self.config.allow_live_trading
                else "requires_explicit_approval"
            ),
            "unsupported_reason": None if monitor["ok"] else monitor["message"],
            "no_synthetic_fallback": True,
            "openalgo_monitor": monitor,
        }

    def _symbol_resolution(
        self,
        *,
        symbol: str,
        exchange: str,
        enabled: bool,
    ) -> dict[str, Any]:
        if not enabled:
            return {
                "ok": False,
                "status": "not_checked",
                "message": "OpenAlgo account readiness failed first.",
                "no_synthetic_fallback": True,
            }
        instruments = InstrumentDiscoveryService(self.config)
        exact = instruments.validate_symbol(symbol=symbol, exchange=exchange)
        if exact["ok"]:
            return exact
        search = instruments.search(query=symbol, exchange=exchange)
        if search["ok"] and search.get("match_count", 0) > 0:
            bounded_search = {
                key: value
                for key, value in search.items()
                if key != "matches"
            }
            return {
                **bounded_search,
                "ok": False,
                "status": "contract_selection_required",
                "message": (
                    "OpenAlgo found matching instruments, but quote/history "
                    "requires choosing one exact OpenAlgo symbol."
                ),
                "candidates": search.get("matches", [])[:8],
            }
        if search["ok"]:
            return {
                **search,
                "ok": False,
                "status": "symbol_not_found",
                "message": (
                    "OpenAlgo could not find a matching instrument for the "
                    "requested symbol and exchange."
                ),
            }
        return {
            **exact,
            "search_status": search.get("status"),
            "search_message": search.get("message"),
        }

    def _client(self) -> OpenAlgoClient:
        return OpenAlgoClient(
            self.config.openalgo_base_url,
            self.config.openalgo_api_key or "",
        )

    def _base_payload(self) -> dict[str, Any]:
        return {
            "provider": "openalgo",
            "base_url": self.config.openalgo_base_url,
            "configured": bool(self.config.openalgo_api_key),
            "credentials_redacted": True,
            "live_trading_enabled": self.config.allow_live_trading,
        }

    def _failed(
        self,
        status: str,
        message: str,
        checks: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **self._base_payload(),
            "ok": False,
            "status": status,
            "safe_failure": True,
            "message": message,
            "no_synthetic_fallback": True,
            "analyzer_mode": False,
            "live_mode": False,
            "checks": {**self._empty_checks(status), **checks},
        }

    def _check_failed(self, status: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "status": status,
            "message": message,
            "no_synthetic_fallback": True,
        }

    def _empty_checks(self, status: str) -> dict[str, Any]:
        return {
            "analyzer": {"ok": False, "status": status},
            "funds": {"ok": False, "status": status},
            "orderbook": {"ok": False, "status": status},
            "tradebook": {"ok": False, "status": status},
            "positionbook": {"ok": False, "status": status},
        }

    def _market_check(
        self,
        check_type: str,
        *,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: str,
        end_date: str,
        enabled: bool,
        disabled_message: str = "OpenAlgo account readiness failed first.",
    ) -> dict[str, Any]:
        if not enabled:
            return {
                "ok": False,
                "status": "not_checked",
                "message": disabled_message,
                "no_synthetic_fallback": True,
            }
        try:
            client = self._client()
            if check_type == "quote":
                response = client.quote(
                    symbol=symbol.upper(),
                    exchange=exchange.upper(),
                )
            elif check_type == "history":
                response = client.historical(
                    symbol=symbol.upper(),
                    exchange=exchange.upper(),
                    interval=interval,
                    start_date=start_date,
                    end_date=end_date,
                )
            else:
                raise ValueError(f"Unsupported market check: {check_type}")
            data = response["data"]
            item_count = len(data) if isinstance(data, list) else 1
            return {
                "ok": True,
                "status": "available",
                "message": f"OpenAlgo {check_type} endpoint returned data.",
                "item_count": item_count,
                "no_synthetic_fallback": True,
            }
        except OpenAlgoAuthenticationError as exc:
            return self._market_failed("authentication_failed", str(exc))
        except OpenAlgoUnavailableError as exc:
            return self._market_failed("unavailable", str(exc))
        except (OpenAlgoResponseError, OpenAlgoError) as exc:
            return self._market_failed("provider_error", str(exc))

    def _market_failed(self, status: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "status": status,
            "message": message,
            "no_synthetic_fallback": True,
        }
