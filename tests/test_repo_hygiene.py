"""Guards against the kinds of rot that fail silently.

Nothing here tests behaviour. Each case pins a claim the codebase makes about
itself — which documents exist, which modules import cleanly — where being
wrong produces no error, just a quietly worse platform.
"""

from __future__ import annotations

import ast
import builtins
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


class RouteModuleImportDepthTest(unittest.TestCase):
    """Handler-local imports must be re-based when a module moves.

    Route handlers were lifted out of ``api.py`` into ``api_routes/``, one
    directory deeper. Module-level imports fail loudly at import time, but an
    import *inside* a handler only runs when that route is called — so neither
    importing the app nor diffing ``/openapi.json`` catches a stale one. Three
    routes shipped broken exactly that way.
    """

    def test_no_single_dot_imports_in_route_modules(self) -> None:
        offenders: list[str] = []
        package = _ROOT / "iimc_trading_platform" / "api_routes"
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level != 1:
                    continue
                # Sibling modules inside the package are legitimately one dot.
                target = (node.module or "").split(".")[0]
                if (package / f"{target}.py").exists() or not target:
                    continue
                offenders.append(f"{path.name}:{node.lineno} from .{node.module}")
        self.assertEqual(
            offenders, [], f"these resolve inside api_routes/, not the package root: {offenders}"
        )


class RouteHandlerSmokeTest(unittest.TestCase):
    """Every GET handler must at least run.

    The route surface being unchanged says nothing about the bodies.
    """

    def test_parameterless_gets_do_not_500(self) -> None:
        import logging
        import tempfile
        import warnings

        from fastapi.testclient import TestClient

        from iimc_trading_platform.api import create_app
        from iimc_trading_platform.config import AppConfig
        from iimc_trading_platform.infrastructure import initialize_database

        warnings.filterwarnings("ignore")
        logging.disable(logging.CRITICAL)
        try:
            tmp = Path(tempfile.mkdtemp())
            db = tmp / "smoke.duckdb"
            initialize_database(db)
            app = create_app(
                AppConfig(database_path=db, artifacts_dir=tmp / "artifacts")
            )
            client = TestClient(app, raise_server_exceptions=False)
            failures = []
            for route in sorted(
                {
                    r.path
                    for r in app.routes
                    if "GET" in getattr(r, "methods", set()) and "{" not in r.path
                }
            ):
                # /ready correctly reports 503 on a database with no history.
                if route == "/ready":
                    continue
                response = client.get(route)
                if response.status_code >= 500:
                    failures.append(f"{route} -> {response.status_code}")
            self.assertEqual(failures, [])
        finally:
            logging.disable(logging.NOTSET)


class ToolCatalogueTest(unittest.TestCase):
    """The catalogue split must not change what a tool is or where it sits."""

    def _registry(self):
        from iimc_trading_platform.tools.registry import build_default_tool_registry

        return build_default_tool_registry(Path("unused.duckdb"))

    def test_catalogue_modules_have_no_unresolved_names(self) -> None:
        """Handlers are lambdas — an unresolved name waits until the call.

        Splitting the catalogue moved tools away from helpers they referenced
        without importing. Building the registry still succeeded, and so did
        importing it; only calling that one tool would have failed. This is the
        static check that found it.
        """
        package = _ROOT / "iimc_trading_platform" / "tools" / "catalog"
        problems: list[str] = []
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            bound = set(dir(builtins))
            for node in ast.walk(tree):
                if isinstance(node, ast.alias):
                    bound.add(node.asname or node.name.split(".")[0])
                elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    bound.add(node.name)
                elif isinstance(node, ast.arg):
                    bound.add(node.arg)
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    bound.add(node.id)
                elif isinstance(node, ast.ExceptHandler) and node.name:
                    bound.add(node.name)
            used = {
                n.id for n in ast.walk(tree)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            }
            problems += [f"{path.name}: {n}" for n in sorted(used - bound)]
        self.assertEqual(problems, [], f"unimported names in the catalogue: {problems}")

    def test_the_registry_keeps_its_public_import_surface(self) -> None:
        """Other modules import the input models from tools.registry."""
        import iimc_trading_platform.tools.registry as registry

        for name in (
            "ToolDefinition",
            "ToolRegistry",
            "KnowledgeSearchInput",
            "MarketQuoteInput",
            "RunIdInput",
            "build_default_tool_registry",
        ):
            self.assertTrue(hasattr(registry, name), f"registry lost {name}")


if __name__ == "__main__":
    unittest.main()
