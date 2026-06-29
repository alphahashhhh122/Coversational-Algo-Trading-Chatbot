from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OpenAlgoError(RuntimeError):
    pass


class OpenAlgoAuthenticationError(OpenAlgoError):
    pass


class OpenAlgoUnavailableError(OpenAlgoError):
    pass


class OpenAlgoResponseError(OpenAlgoError):
    pass


@dataclass(frozen=True)
class OpenAlgoClient:
    base_url: str
    api_key: str
    timeout_seconds: float = 10.0

    def account_snapshot(self, snapshot_type: str) -> dict[str, Any]:
        allowed = {
            "analyzer",
            "funds",
            "positionbook",
            "orderbook",
            "tradebook",
        }
        if snapshot_type not in allowed:
            raise ValueError(
                f"Unsupported OpenAlgo snapshot type: {snapshot_type}"
            )
        return self._post(f"/api/v1/{snapshot_type}", {})

    def analyzer_status(self) -> dict[str, Any]:
        response = self._post("/api/v1/analyzer", {})
        data = response.get("data")
        if not isinstance(data, dict):
            raise OpenAlgoResponseError(
                "OpenAlgo analyzer status omitted its data object"
            )
        return data

    def place_sandbox_order(
        self,
        *,
        strategy: str,
        symbol: str,
        action: str,
        exchange: str,
        price_type: str,
        product: str,
        quantity: int,
        price: float = 0.0,
        trigger_price: float = 0.0,
    ) -> dict[str, Any]:
        analyzer = self.analyzer_status()
        if (
            analyzer.get("analyze_mode") is not True
            or analyzer.get("mode") != "analyze"
        ):
            raise OpenAlgoResponseError(
                "Sandbox submission refused because OpenAlgo analyzer mode "
                "is not active"
            )
        response = self._post(
            "/api/v1/placeorder",
            {
                "strategy": strategy,
                "symbol": symbol,
                "action": action,
                "exchange": exchange,
                "pricetype": price_type,
                "product": product,
                "quantity": quantity,
                "price": price,
                "trigger_price": trigger_price,
                "disclosed_quantity": 0,
            },
        )
        if response.get("mode") != "analyze":
            raise OpenAlgoResponseError(
                "OpenAlgo did not confirm analyzer mode for the submitted order"
            )
        if not response.get("orderid"):
            raise OpenAlgoResponseError(
                "OpenAlgo sandbox response omitted orderid"
            )
        return response

    def place_live_order(
        self,
        *,
        strategy: str,
        symbol: str,
        action: str,
        exchange: str,
        price_type: str,
        product: str,
        quantity: int,
        price: float = 0.0,
        trigger_price: float = 0.0,
    ) -> dict[str, Any]:
        response = self._post(
            "/api/v1/placeorder",
            {
                "strategy": strategy,
                "symbol": symbol,
                "action": action,
                "exchange": exchange,
                "pricetype": price_type,
                "product": product,
                "quantity": quantity,
                "price": price,
                "trigger_price": trigger_price,
                "disclosed_quantity": 0,
            },
        )
        if not response.get("orderid"):
            raise OpenAlgoResponseError(
                "OpenAlgo live order response omitted orderid"
            )
        if response.get("mode") == "analyze":
            raise OpenAlgoResponseError(
                "OpenAlgo reported analyzer mode for a live order request"
            )
        return response

    def order_status(
        self,
        *,
        order_id: str,
        strategy: str,
    ) -> dict[str, Any]:
        response = self._post(
            "/api/v1/orderstatus",
            {"orderid": order_id, "strategy": strategy},
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise OpenAlgoResponseError(
                "OpenAlgo order status omitted its data object"
            )
        return data

    def cancel_order(
        self,
        *,
        order_id: str,
        strategy: str,
    ) -> dict[str, Any]:
        response = self._post(
            "/api/v1/cancelorder",
            {"orderid": order_id, "strategy": strategy},
        )
        if response.get("orderid") is None:
            raise OpenAlgoResponseError(
                "OpenAlgo cancel response omitted orderid"
            )
        return response

    def quote(
        self,
        *,
        symbol: str,
        exchange: str,
    ) -> dict[str, Any]:
        response = self._post(
            "/api/v1/quotes",
            {"symbol": symbol, "exchange": exchange},
        )
        data = response.get("data")
        if data is None:
            raise OpenAlgoResponseError(
                "OpenAlgo quote response omitted its data field"
            )
        return {"data": data, "raw": response}

    def symbol_details(
        self,
        *,
        symbol: str,
        exchange: str,
    ) -> dict[str, Any]:
        response = self._post(
            "/api/v1/symbol",
            {"symbol": symbol, "exchange": exchange},
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise OpenAlgoResponseError(
                "OpenAlgo symbol response omitted its data object"
            )
        return {"data": data, "raw": response}

    def search_symbols(
        self,
        *,
        query: str,
        exchange: str,
    ) -> dict[str, Any]:
        response = self._post(
            "/api/v1/search",
            {"query": query, "exchange": exchange},
        )
        data = response.get("data")
        if not isinstance(data, list):
            raise OpenAlgoResponseError(
                "OpenAlgo search response omitted its data array"
            )
        return {"data": data, "raw": response}

    def option_symbol(
        self,
        *,
        underlying: str,
        exchange: str,
        expiry_date: str,
        offset: str,
        option_type: str,
    ) -> dict[str, Any]:
        response = self._post(
            "/api/v1/optionsymbol",
            {
                "underlying": underlying,
                "exchange": exchange,
                "expiry_date": expiry_date,
                "offset": offset,
                "option_type": option_type,
            },
        )
        symbol = response.get("symbol")
        exchange_value = response.get("exchange")
        if not symbol or not exchange_value:
            raise OpenAlgoResponseError(
                "OpenAlgo optionsymbol response omitted symbol or exchange"
            )
        return response

    def historical(
        self,
        *,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        response = self._post(
            "/api/v1/history",
            {
                "symbol": symbol,
                "exchange": exchange,
                "interval": interval,
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        data = response.get("data")
        if data is None:
            raise OpenAlgoResponseError(
                "OpenAlgo history response omitted its data field"
            )
        return {"data": data, "raw": response}

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        request_payload = {"apikey": self.api_key, **payload}
        request = Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                raise OpenAlgoAuthenticationError(
                    "OpenAlgo rejected the configured API key"
                ) from exc
            raise OpenAlgoResponseError(
                f"OpenAlgo returned HTTP {exc.code}: {body[:300]}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise OpenAlgoUnavailableError(
                "OpenAlgo is unavailable at the configured base URL"
            ) from exc

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenAlgoResponseError(
                "OpenAlgo returned a non-JSON response"
            ) from exc
        if not isinstance(decoded, dict):
            raise OpenAlgoResponseError(
                "OpenAlgo returned an unexpected response shape"
            )
        if decoded.get("status") != "success":
            message = str(decoded.get("message", "Unknown OpenAlgo error"))
            if "api" in message.lower() and "key" in message.lower():
                raise OpenAlgoAuthenticationError(
                    "OpenAlgo rejected the configured API key"
                )
            raise OpenAlgoResponseError(message)
        return decoded
