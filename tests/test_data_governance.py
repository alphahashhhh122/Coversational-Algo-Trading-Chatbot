from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.freshness_service import FreshnessService
from iimc_trading_platform.services.knowledge_service import KnowledgeService


class DataGovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "governance.duckdb"
        initialize_database(self.db_path)
        self.dataset_id = "historical_nifty"
        self.reference_time = datetime(2026, 6, 19, 12, 0)
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO data_catalog VALUES (
                    ?, 'market_data', 'options_ohlcv', 'NIFTY', 'NFO',
                    '5m', ?, ?, 100, 'options_ohlcv', 'source_1',
                    'clean_with_warnings', NULL, ?
                )
                """,
                [
                    self.dataset_id,
                    self.reference_time - timedelta(days=30),
                    self.reference_time - timedelta(days=29),
                    self.reference_time - timedelta(days=29),
                ],
            )
        finally:
            con.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_freshness_is_purpose_aware_and_audited(self) -> None:
        service = FreshnessService(self.db_path)
        historical = service.assess(
            self.dataset_id,
            "historical_research",
            reference_time=self.reference_time,
        )
        current = service.assess(
            self.dataset_id,
            "current_market",
            reference_time=self.reference_time,
        )

        self.assertEqual(historical["status"], "fresh")
        self.assertEqual(current["status"], "stale")
        con = connect(self.db_path)
        try:
            assessment_count = con.execute(
                "SELECT COUNT(*) FROM freshness_assessments"
            ).fetchone()[0]
            policy_count = con.execute(
                "SELECT COUNT(*) FROM dataset_freshness_policies"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(assessment_count, 2)
        self.assertEqual(policy_count, 2)

    def test_knowledge_index_is_idempotent_and_retrieval_has_provenance(
        self,
    ) -> None:
        service = KnowledgeService(self.db_path)
        first = service.index_text(
            title="Risk Architecture",
            source_uri="docs/risk.md",
            text=(
                "The risk service evaluates quantity, notional, confidence, "
                "loss per trade, and daily loss before order creation."
            ),
        )
        repeated = service.index_text(
            title="Risk Architecture",
            source_uri="docs/risk.md",
            text=(
                "The risk service evaluates quantity, notional, confidence, "
                "loss per trade, and daily loss before order creation."
            ),
        )
        revised = service.index_text(
            title="Risk Architecture",
            source_uri="docs/risk.md",
            text=(
                "The risk service evaluates quantity, notional, confidence, "
                "loss per trade, daily loss, and execution mode."
            ),
        )
        result = service.search(
            "How is daily loss checked before an order?",
            session_id="session_1",
        )

        self.assertEqual(first["document_id"], repeated["document_id"])
        self.assertEqual(first["document_id"], revised["document_id"])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(
            result["matches"][0]["document_id"],
            first["document_id"],
        )
        self.assertTrue(result["matches"][0]["chunk_id"].startswith("chunk_"))
        con = connect(self.db_path)
        try:
            retrieval = con.execute(
                """
                SELECT session_id, method
                FROM retrieval_events
                WHERE retrieval_id = ?
                """,
                [result["retrieval_id"]],
            ).fetchone()
        finally:
            con.close()
        self.assertEqual(
            retrieval,
            ("session_1", "lexical_bm25_v2"),
        )


if __name__ == "__main__":
    unittest.main()
