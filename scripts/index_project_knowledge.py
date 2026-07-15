from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.knowledge_service import KnowledgeService


DEFAULT_DOCUMENTS = [
    Path("README.md"),
    Path("docs/ARCHITECTURE.md"),
    Path("docs/DATA_DOMAINS.md"),
    Path("docs/SECURITY_AND_SECRETS.md"),
    Path("docs/OPENALGO_SANDBOX_BRIDGE.md"),
    Path("docs/OPERATOR_RUNBOOK.md"),
    Path("docs/OPERATIONS_FAILURE_RUNBOOK.md"),
    Path("docs/PRODUCTION_READINESS.md"),
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Index the curated project knowledge corpus."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/iimc_platform.duckdb"),
    )
    args = parser.parse_args()
    initialize_database(args.database)
    service = KnowledgeService(args.database)
    indexed = []
    for path in DEFAULT_DOCUMENTS:
        if not path.exists():
            continue
        indexed.append(
            service.index_text(
                title=path.stem.replace("_", " "),
                source_uri=str(path.resolve()),
                text=path.read_text(encoding="utf-8"),
                document_type=path.suffix.removeprefix(".") or "text",
                metadata={"corpus": "curated_project_docs"},
            )
        )
    print(
        json.dumps(
            {
                "indexed": indexed,
                "document_count": len(
                    service.list_documents()["documents"]
                ),
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
