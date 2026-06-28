from __future__ import annotations

import argparse
import json
from pathlib import Path

from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.knowledge_service import KnowledgeService


DEFAULT_DOCUMENTS = [
    Path("docs/AI_FIRST_TARGET_ARCHITECTURE.md"),
    Path("docs/DATA_DOMAINS.md"),
    Path("docs/SECURITY_AND_SECRETS.md"),
    Path("docs/CLAUDE_HANDOFF_AUDIT.md"),
    Path("docs/OPENALGO_SANDBOX_BRIDGE.md"),
    Path("docs/OPERATOR_WORKSPACE.md"),
    Path("docs/OPERATIONS_FAILURE_RUNBOOK.md"),
    Path("PROJECT_PLAN.md"),
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
