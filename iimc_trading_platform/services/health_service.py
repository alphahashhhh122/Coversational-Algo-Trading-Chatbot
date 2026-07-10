from __future__ import annotations

from typing import Any

from ..config import AppConfig
from ..infrastructure import CORE_TABLES, list_tables


def foundation_health(config: AppConfig) -> dict[str, Any]:
    database_exists = config.database_path.exists()
    database_error: str | None = None
    existing_tables: set[str] = set()
    if database_exists:
        try:
            existing_tables = set(list_tables(config.database_path))
        except Exception as exc:
            database_error = f"Database inspection failed: {type(exc).__name__}"

    missing_tables = sorted(set(CORE_TABLES) - existing_tables)
    checks = {
        "database_exists": database_exists,
        "database_accessible": database_exists and database_error is None,
        "core_schema_complete": database_error is None and not missing_tables,
        "artifacts_directory_exists": config.artifacts_dir.exists(),
        "openalgo_root_exists": config.openalgo_root.exists(),
        "llm_provider": config.llm_provider,
        "groq_api_key_configured": bool(config.groq_api_key),
        "openai_api_key_configured": bool(config.openai_api_key),
        "real_llm_required": config.require_real_llm,
        "openalgo_api_key_configured": bool(config.openalgo_api_key),
        "live_trading_disabled": not config.allow_live_trading,
        "authentication_required": config.auth_required,
        "auth_secret_configured": bool(config.auth_secret),
    }
    required_checks = {
        "database_exists",
        "database_accessible",
        "core_schema_complete",
    }
    healthy = all(checks[name] for name in required_checks)
    notes = []
    if config.llm_provider == "groq" and not config.groq_api_key:
        if config.require_real_llm:
            notes.append("GROQ_API_KEY is absent; real LLM orchestration is blocked.")
        else:
            notes.append("GROQ_API_KEY is absent; deterministic local routing is available for tests only.")
    elif config.llm_provider != "groq" and not config.openai_api_key:
        if config.require_real_llm:
            notes.append("OPENAI_API_KEY is absent; real LLM orchestration is blocked.")
        else:
            notes.append("OPENAI_API_KEY is absent; deterministic local routing is available for tests only.")
    if not config.openalgo_api_key:
        notes.append(
            "OPENALGO_API_KEY is absent; authenticated account snapshots are disabled."
        )
    if not config.allow_live_trading:
        notes.append(
            "Live trading is disabled; research and read-only workflows remain available."
        )
    else:
        notes.append(
            "Live trading is enabled; orders still require broker readiness, "
            "risk checks, and explicit approval policy."
        )
    if config.auth_required and not config.auth_secret:
        notes.append(
            "Authentication is required but IIMC_AUTH_SECRET is absent."
        )
    return {
        "status": "healthy" if healthy else "unhealthy",
        "checks": checks,
        "missing_tables": missing_tables,
        "database_error": database_error,
        "notes": notes,
    }
