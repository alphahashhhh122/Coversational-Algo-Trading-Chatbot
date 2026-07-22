from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from iimc_trading_platform.infrastructure.database import initialize_database
from iimc_trading_platform.orchestration import grounded_tool_response
from iimc_trading_platform.services.memory_service import MemoryService


class MemoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)  # let DuckDB create it fresh
        self.path = Path(path)
        initialize_database(self.path)
        self.memory = MemoryService(self.path)

    def tearDown(self) -> None:
        for suffix in ("", ".wal"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass

    def test_notes_accumulate_and_recall(self) -> None:
        self.memory.remember_note("I prefer low-risk swing trades")
        self.memory.remember_note("Only trade NIFTY 50 names")
        recalled = self.memory.recall()
        contents = [n["content"] for n in recalled["notes"]]
        self.assertIn("I prefer low-risk swing trades", contents)
        self.assertIn("Only trade NIFTY 50 names", contents)
        self.assertIsNone(recalled["research"])

    def test_empty_note_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.memory.remember_note("   ")

    def test_research_upserts_one_per_symbol(self) -> None:
        self.memory.save_research("RELIANCE", "first summary")
        self.memory.save_research("RELIANCE", "second summary")
        got = self.memory.get_research("reliance")
        self.assertIsNotNone(got)
        self.assertEqual(got["content"], "second summary")
        # Recall keyed by a symbol pulls its research.
        recalled = self.memory.recall("what did we find on RELIANCE")
        self.assertEqual(recalled["research"]["content"], "second summary")

    def test_grounded_render_remember_and_recall(self) -> None:
        stored = self.memory.remember_note("hedge with index puts")
        answer = grounded_tool_response("remember", stored | {"content": "hedge with index puts"})
        self.assertIn("remember", answer.lower())
        self.assertIn("hedge with index puts", answer)

        recall = self.memory.recall()
        text = grounded_tool_response("recall_memory", recall)
        self.assertIn("hedge with index puts", text)

    def test_recall_with_no_notes_is_honest(self) -> None:
        text = grounded_tool_response("recall_memory", self.memory.recall())
        self.assertIn("don't have any saved notes", text.lower())


if __name__ == "__main__":
    unittest.main()
