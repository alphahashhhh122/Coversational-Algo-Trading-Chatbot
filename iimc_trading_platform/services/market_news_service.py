from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import AppConfig
from ..db import connect


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MarketNewsService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.db_path = config.database_path
        self.artifacts_dir = config.artifacts_dir

    def status(self) -> dict[str, Any]:
        return {
            "ok": bool(
                self.config.market_news_provider
                and self.config.market_news_api_url
            ),
            "news_configured": bool(
                self.config.market_news_provider
                and self.config.market_news_api_url
            ),
            "provider": self.config.market_news_provider,
            "api_url_configured": bool(self.config.market_news_api_url),
            "api_key_configured": bool(self.config.market_news_api_key),
            "max_articles": self.config.market_news_max_articles,
            "no_synthetic_fallback": True,
        }

    def latest(self, limit: int = 20) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        con = connect(self.db_path)
        try:
            rows = con.execute(
                """
                SELECT article_id, title, source, source_url, published_at,
                       retrieved_at, provider, query, symbol, sha256
                FROM market_news_articles
                ORDER BY COALESCE(published_at, retrieved_at) DESC,
                         retrieved_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            con.close()
        return {
            **self.status(),
            "articles": [
                {
                    "article_id": row[0],
                    "title": row[1],
                    "source": row[2],
                    "source_url": row[3],
                    "published_at": row[4],
                    "retrieved_at": row[5],
                    "provider": row[6],
                    "query": row[7],
                    "symbol": row[8],
                    "sha256": row[9],
                }
                for row in rows
            ],
        }

    def fetch(
        self,
        *,
        query: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        status = self.status()
        if not status["news_configured"]:
            return {
                **status,
                "ok": False,
                "status": "news_provider_not_configured",
                "safe_failure": True,
                "message": "Market news provider is not configured.",
                "articles": [],
            }

        retrieved_at = utc_now()
        fetch_id = f"news_fetch_{uuid.uuid4().hex[:12]}"
        request = self._request(query=query, symbol=symbol)
        try:
            with urlopen(
                request,
                timeout=self.config.market_news_timeout_seconds,
            ) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            redacted = self._redact_secret(body[:500])
            self._record_fetch(
                fetch_id=fetch_id,
                provider=self.config.market_news_provider or "unknown",
                query=query,
                symbol=symbol,
                raw_artifact_path="",
                article_count=0,
                status="failed",
                error_message=f"HTTP {exc.code}: {redacted}",
                retrieved_at=retrieved_at,
            )
            return {
                **status,
                "ok": False,
                "status": "provider_error",
                "safe_failure": True,
                "message": "Market news provider request failed.",
                "provider_status_code": exc.code,
                "provider_error": redacted,
                "no_synthetic_fallback": True,
                "articles": [],
            }
        except Exception as exc:
            self._record_fetch(
                fetch_id=fetch_id,
                provider=self.config.market_news_provider or "unknown",
                query=query,
                symbol=symbol,
                raw_artifact_path="",
                article_count=0,
                status="failed",
                error_message=f"{type(exc).__name__}: {exc}",
                retrieved_at=retrieved_at,
            )
            return {
                **status,
                "ok": False,
                "status": "provider_error",
                "safe_failure": True,
                "message": "Market news provider request failed.",
                "no_synthetic_fallback": True,
                "articles": [],
            }

        artifact_path = self._write_raw(fetch_id, raw)
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            self._record_fetch(
                fetch_id=fetch_id,
                provider=self.config.market_news_provider or "unknown",
                query=query,
                symbol=symbol,
                raw_artifact_path=str(artifact_path),
                article_count=0,
                status="failed",
                error_message="Provider returned non-JSON news payload",
                retrieved_at=retrieved_at,
            )
            return {
                **status,
                "ok": False,
                "status": "provider_error",
                "safe_failure": True,
                "message": "Market news provider returned non-JSON data.",
                "no_synthetic_fallback": True,
                "articles": [],
            }
        articles = self._normalize_articles(
            decoded,
            query=query,
            symbol=symbol,
            retrieved_at=retrieved_at,
        )
        self._record_fetch(
            fetch_id=fetch_id,
            provider=self.config.market_news_provider or "unknown",
            query=query,
            symbol=symbol,
            raw_artifact_path=str(artifact_path),
            article_count=len(articles),
            status="succeeded",
            error_message=None,
            retrieved_at=retrieved_at,
        )
        inserted = self._store_articles(fetch_id, articles)
        return {
            **status,
            "ok": True,
            "status": "succeeded",
            "safe_failure": False,
            "fetch_id": fetch_id,
            "raw_artifact_path": str(artifact_path),
            "retrieved_at": retrieved_at,
            "query": query,
            "symbol": symbol,
            "article_count": len(articles),
            "inserted_count": inserted,
            "articles": articles,
        }

    def _request(self, *, query: str | None, symbol: str | None) -> Request:
        url = self._url(query=query, symbol=symbol)
        provider = (self.config.market_news_provider or "").lower()
        headers = {"User-Agent": "iimc-trading-platform/0.2"}
        if provider in {"eventregistry", "event_registry"}:
            payload = {
                "action": "getArticles",
                "keyword": self._event_registry_keyword(
                    query=query,
                    symbol=symbol,
                ),
                "lang": "eng",
                "articlesPage": 1,
                "articlesCount": self.config.market_news_max_articles,
                "articlesSortBy": "date",
                "articlesSortByAsc": False,
                "articlesArticleBodyLen": -1,
                "resultType": "articles",
            }
            if self.config.market_news_api_key:
                payload["apiKey"] = self.config.market_news_api_key
            return Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={**headers, "Content-Type": "application/json"},
            )
        request = Request(url, method="GET", headers=headers)
        if self.config.market_news_api_key and provider != "newsapi":
            request.add_header("X-API-Key", self.config.market_news_api_key)
        return request

    def _url(self, *, query: str | None, symbol: str | None) -> str:
        assert self.config.market_news_api_url is not None
        provider = (self.config.market_news_provider or "").lower()
        if provider in {"eventregistry", "event_registry"}:
            return self.config.market_news_api_url
        if provider == "newsapi":
            params = {
                "q": query or symbol or "market",
                "pageSize": str(self.config.market_news_max_articles),
                "language": "en",
                "sortBy": "publishedAt",
            }
            if self.config.market_news_api_key:
                params["apiKey"] = self.config.market_news_api_key
        else:
            params = {
                "q": query or symbol or "market",
                "symbol": symbol or "",
                "limit": str(self.config.market_news_max_articles),
            }
        separator = "&" if "?" in self.config.market_news_api_url else "?"
        return f"{self.config.market_news_api_url}{separator}{urlencode(params)}"

    def _event_registry_keyword(
        self,
        *,
        query: str | None,
        symbol: str | None,
    ) -> str:
        raw = " ".join(part for part in [query, symbol] if part).strip()
        if not raw:
            return "market"
        normalized = raw.lower()
        has_india = "india" in normalized or "indian" in normalized
        has_market = "market" in normalized or "stock" in normalized
        has_benchmark = "nifty" in normalized or "sensex" in normalized
        if has_india and has_market and has_benchmark:
            return "Indian stock market"
        if "sensex" in normalized and has_market:
            return "Indian stock market"
        if normalized in {"nifty", "nifty 50"}:
            return "NIFTY stock market"
        # A bare ticker/common word (e.g. "reliance") returns noisy results;
        # the full company name ("Reliance Industries") is far more specific.
        from .instrument_names import company_name

        name = company_name(symbol) if symbol else None
        if name:
            return name
        return query or symbol or "market"

    def _redact_secret(self, value: str) -> str:
        secret = self.config.market_news_api_key
        if secret:
            return value.replace(secret, "[REDACTED]")
        return value

    def _write_raw(self, fetch_id: str, raw: str) -> Path:
        directory = self.artifacts_dir / "market_news"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{fetch_id}.json"
        path.write_text(raw, encoding="utf-8")
        return path

    def _normalize_articles(
        self,
        decoded: dict[str, Any],
        *,
        query: str | None,
        symbol: str | None,
        retrieved_at: datetime,
    ) -> list[dict[str, Any]]:
        raw_articles = decoded.get("articles")
        if isinstance(raw_articles, dict):
            raw_articles = raw_articles.get("results", [])
        if raw_articles is None:
            raw_articles = decoded.get("data", [])
        if not isinstance(raw_articles, list):
            raw_articles = []
        articles: list[dict[str, Any]] = []
        for raw in raw_articles[: self.config.market_news_max_articles]:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or raw.get("headline") or "").strip()
            if not title:
                continue
            source = raw.get("source")
            if isinstance(source, dict):
                source = source.get("name") or source.get("title") or source.get("uri")
            source_url = raw.get("url") or raw.get("source_url") or raw.get("link")
            published = (
                raw.get("published_at")
                or raw.get("publishedAt")
                or raw.get("dateTime")
                or raw.get("date")
            )
            canonical = json.dumps(
                {
                    "title": title,
                    "source": source or "unknown",
                    "source_url": source_url,
                    "published_at": published,
                },
                sort_keys=True,
                default=str,
            )
            articles.append(
                {
                    "article_id": f"news_{uuid.uuid4().hex[:12]}",
                    "title": title,
                    "source": str(source or "unknown"),
                    "source_url": source_url,
                    "published_at": published,
                    "retrieved_at": retrieved_at,
                    "provider": self.config.market_news_provider,
                    "query": query,
                    "symbol": symbol,
                    "sha256": hashlib.sha256(
                        canonical.encode("utf-8")
                    ).hexdigest(),
                    "raw": raw,
                }
            )
        return articles

    def _record_fetch(
        self,
        *,
        fetch_id: str,
        provider: str,
        query: str | None,
        symbol: str | None,
        raw_artifact_path: str,
        article_count: int,
        status: str,
        error_message: str | None,
        retrieved_at: datetime,
    ) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO market_news_fetches VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    fetch_id,
                    provider,
                    query,
                    symbol,
                    raw_artifact_path,
                    article_count,
                    status,
                    error_message,
                    retrieved_at,
                ],
            )
        finally:
            con.close()

    def _store_articles(
        self,
        fetch_id: str,
        articles: list[dict[str, Any]],
    ) -> int:
        con = connect(self.db_path)
        inserted = 0
        try:
            for article in articles:
                before = con.execute(
                    "SELECT COUNT(*) FROM market_news_articles WHERE sha256 = ?",
                    [article["sha256"]],
                ).fetchone()[0]
                con.execute(
                    """
                    INSERT OR IGNORE INTO market_news_articles VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    [
                        article["article_id"],
                        fetch_id,
                        article["title"],
                        article["source"],
                        article["source_url"],
                        article["published_at"],
                        article["retrieved_at"],
                        article["provider"],
                        article["query"],
                        article["symbol"],
                        article["sha256"],
                        json.dumps(article["raw"], sort_keys=True, default=str),
                    ],
                )
                after = con.execute(
                    "SELECT COUNT(*) FROM market_news_articles WHERE sha256 = ?",
                    [article["sha256"]],
                ).fetchone()[0]
                inserted += int(after > before)
        finally:
            con.close()
        return inserted
