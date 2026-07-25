"""A small typed client for the Agentic Trading Lab HTTP API.

Deliberately dependency-free (``urllib`` from the standard library) so the SDK
installs anywhere the platform does and adds nothing to the runtime footprint.

    from iimc_trading_platform.sdk import ATLClient

    atl = ATLClient("http://127.0.0.1:8000")
    for agent in atl.list_agents():
        print(agent["name"], agent["category"])

    run = atl.run_agent("market_researcher", symbol="RELIANCE")
    print(run["status"], run["gaps"])

    for row in atl.leaderboard()["ranked"]:
        print(row["rank"], row["name"], row["composite"], "->", row["run_id"])

**There is no order-approval method on this client, and there never will be.**
The API it wraps exposes research, evaluation, and arena surfaces only;
approving an order is a deliberate human action in the web UI.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

__all__ = ["ATLClient", "ATLError"]

_TIMEOUT = 120.0


class ATLError(RuntimeError):
    """An API call failed. Carries the status code when there was one."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ATLClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        token: str | None = None,
        timeout: float = _TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # -- agents ---------------------------------------------------------------

    def list_agents(self, category: str | None = None) -> list[dict[str, Any]]:
        """Every registered agent, optionally filtered by category."""
        query = f"?category={category}" if category else ""
        return self._get(f"/agents{query}")["agents"]

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        return self._get(f"/agents/{agent_id}")

    def run_agent(
        self,
        agent_id: str,
        *,
        symbol: str | None = None,
        symbols: list[str] | None = None,
        exchange: str = "NSE",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run an agent and return findings, evidence, gaps, and its scorecard."""
        return self._post(
            f"/agents/{agent_id}/run",
            {
                "task_type": "sdk",
                "symbol": symbol,
                "symbols": symbols or [],
                "exchange": exchange,
                "params": params or {},
            },
        )

    def agent_runs(self, agent_id: str) -> list[dict[str, Any]]:
        """Past runs, each with the evidence it was scored on."""
        return self._get(f"/agents/{agent_id}/runs")["runs"]

    # -- evaluation -----------------------------------------------------------

    def leaderboard(self, category: str | None = None) -> dict[str, Any]:
        """Ranked agents plus the `unranked` ones and why they're inconclusive."""
        query = f"?category={category}" if category else ""
        return self._get(f"/leaderboard{query}")

    # -- arena ----------------------------------------------------------------

    def seasons(self) -> list[dict[str, Any]]:
        return self._get("/arena/seasons")["seasons"]

    def create_season(
        self, name: str, symbol: str, exchange: str = "NSE"
    ) -> dict[str, Any]:
        return self._post(
            "/arena/seasons",
            {"name": name, "symbol": symbol, "exchange": exchange},
        )

    def enroll(
        self,
        season_id: str,
        agent_id: str,
        *,
        strategy_name: str = "ema_crossover",
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._post(
            f"/arena/seasons/{season_id}/enroll",
            {
                "agent_id": agent_id,
                "strategy_name": strategy_name,
                "parameters": parameters or {},
            },
        )

    def tick(self, season_id: str) -> dict[str, Any]:
        """Advance the season a day on real data."""
        return self._post(f"/arena/seasons/{season_id}/tick", {})

    def standings(self, season_id: str) -> dict[str, Any]:
        return self._get(f"/arena/seasons/{season_id}/standings")

    # -- committee ------------------------------------------------------------

    def committee(
        self,
        symbol: str,
        *,
        exchange: str = "NSE",
        members: list[str] | None = None,
    ) -> dict[str, Any]:
        """A multi-agent brief. Disagreements are reported, never averaged."""
        payload: dict[str, Any] = {"symbol": symbol, "exchange": exchange}
        if members:
            payload["members"] = members
        return self._post("/committee", payload)

    # -- transport ------------------------------------------------------------

    def _get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path, None)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, body)

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise ATLError(
                f"{method} {path} failed ({exc.code}): {detail}", exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise ATLError(
                f"Could not reach the platform at {self.base_url} ({exc.reason}). "
                "Is the server running?"
            ) from exc
