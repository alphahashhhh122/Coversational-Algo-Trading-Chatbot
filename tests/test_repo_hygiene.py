"""Guards against the kinds of rot that fail silently.

Nothing here tests behaviour. Each case pins a claim the codebase makes about
itself — which documents exist, which modules import cleanly — where being
wrong produces no error, just a quietly worse platform.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from iimc_trading_platform.services.operations_service import CURATED_DOCUMENTS

_ROOT = Path(__file__).resolve().parent.parent


class CuratedDocumentsTest(unittest.TestCase):
    def test_every_curated_document_exists(self) -> None:
        """The knowledge sync skips missing files without complaining.

        Five of these once named files that had been renamed or removed, so the
        corpus agents search was three documents deep while the list claimed
        eight — and nothing anywhere said so.
        """
        missing = [str(p) for p in CURATED_DOCUMENTS if not (_ROOT / p).exists()]
        self.assertEqual(missing, [], f"curated documents not on disk: {missing}")

    def test_the_list_is_not_empty(self) -> None:
        self.assertTrue(CURATED_DOCUMENTS)


class DocumentationLinkTest(unittest.TestCase):
    def test_docs_do_not_reference_files_that_are_gone(self) -> None:
        """A doc pointing at a deleted doc sends the reader nowhere."""
        pattern = re.compile(r"`([A-Za-z0-9_/]+\.md)`")
        broken: list[str] = []
        for doc in [*(_ROOT / "docs").glob("*.md"), _ROOT / "README.md"]:
            for match in pattern.finditer(doc.read_text(encoding="utf-8")):
                target = match.group(1)
                name = Path(target).name
                if not list(_ROOT.rglob(name)):
                    broken.append(f"{doc.name} -> {target}")
        self.assertEqual(broken, [], f"broken doc references: {broken}")


class ModuleImportTest(unittest.TestCase):
    def test_every_module_parses(self) -> None:
        """A syntax error in a rarely imported module should not wait for a demo."""
        failures: list[str] = []
        for path in (_ROOT / "iimc_trading_platform").rglob("*.py"):
            try:
                ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:  # pragma: no cover - a failure is the point
                failures.append(f"{path.name}: {exc}")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
