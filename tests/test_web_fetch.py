from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.orchestration import OfflineOrchestrator
from iimc_trading_platform.services.knowledge_service import (
    KnowledgeService,
    _extract_web_text,
)
from iimc_trading_platform.tools.registry import build_default_tool_registry

_HTML = b"""
<html><head><title>Acme FY26 Results</title>
<style>.x{color:red}</style></head>
<body><script>alert(1)</script>
<h1>Acme Industries FY26</h1>
<p>Revenue grew 18 percent to 4,800 crore.</p>
<p>The board declared a dividend of 12 rupees per share.</p>
</body></html>
"""


class _FakeResponse:
    def __init__(self, data: bytes, url: str) -> None:
        self._buffer = io.BytesIO(data)
        self._url = url

    def read(self, limit: int = -1) -> bytes:
        return self._buffer.read(limit)

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class WebFetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "web.duckdb"
        initialize_database(self.db_path)
        self.service = KnowledgeService(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_extract_web_text_strips_markup(self) -> None:
        title, text = _extract_web_text(_HTML.decode())

        self.assertEqual(title, "Acme FY26 Results")
        self.assertIn("Revenue grew 18 percent", text)
        self.assertNotIn("alert(1)", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("<p>", text)

    def test_fetch_and_index_url_stores_document(self) -> None:
        with patch(
            "iimc_trading_platform.services.knowledge_service."
            "urllib.request.urlopen",
            return_value=_FakeResponse(_HTML, "https://example.com/acme"),
        ), patch(
            "iimc_trading_platform.services.knowledge_service."
            "socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ):
            result = self.service.fetch_and_index_url(
                "https://example.com/acme"
            )

        self.assertEqual(result["title"], "Acme FY26 Results")
        self.assertEqual(result["source_url"], "https://example.com/acme")
        search = self.service.search("dividend rupees per share")
        self.assertTrue(
            any("dividend" in m["content"] for m in search["matches"])
        )

    def test_private_hosts_are_blocked(self) -> None:
        for url in (
            "http://127.0.0.1/admin",
            "http://192.168.1.10/x",
            "http://10.0.0.5/x",
        ):
            with self.assertRaises(ValueError):
                self.service.fetch_and_index_url(url)

    def test_non_http_scheme_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.fetch_and_index_url("file:///etc/passwd")

    def test_router_routes_url_fetch(self) -> None:
        registry = build_default_tool_registry(Path("unused.duckdb"))
        decision = OfflineOrchestrator().select_tool(
            "Fetch https://example.com/annual-report.html and store it",
            [],
            registry,
        )
        self.assertEqual(decision.tool_name, "fetch_web_document")
        self.assertEqual(
            decision.arguments["url"],
            "https://example.com/annual-report.html",
        )


if __name__ == "__main__":
    unittest.main()
