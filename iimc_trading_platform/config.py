from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    app_name: str = "IIMC Conversational Algo-Trading Platform"
    environment: str = "local"
    log_level: str = "INFO"
    database_path: Path = Path("data/iimc_platform.duckdb")
    artifacts_dir: Path = Path("artifacts")
    strategy_plugin_dir: Path = Path("strategy_plugins")
    openalgo_root: Path = Path.home() / "openalgo"
    openalgo_base_url: str = "http://127.0.0.1:5000"
    llm_provider: str = "groq"
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_fallback_model: str | None = "llama-3.1-8b-instant"
    require_real_llm: bool = False
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.5"
    openalgo_api_key: str | None = None
    allow_live_trading: bool = False
    auth_required: bool = False
    auth_secret: str | None = None
    session_ttl_minutes: int = 480
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    cors_origins: tuple[str, ...] = ()
    rate_limit_per_minute: int = 120
    max_request_bytes: int = 1_000_000
    otel_enabled: bool = False
    otel_service_name: str = "iimc-trading-platform"
    otel_exporter_otlp_endpoint: str | None = None
    otel_sample_ratio: float = 1.0
    otel_excluded_urls: str = "live,health,ready,metrics"
    market_news_provider: str | None = None
    market_news_api_url: str | None = None
    market_news_api_key: str | None = None
    market_news_timeout_seconds: float = 10.0
    market_news_max_articles: int = 10
    require_paper_approval: bool = False
    paper_signal_max_age_minutes: int = 20

    def __post_init__(self) -> None:
        if not 0.0 <= self.otel_sample_ratio <= 1.0:
            raise ValueError("otel_sample_ratio must be between 0 and 1")
        if self.paper_signal_max_age_minutes < 0:
            raise ValueError("paper_signal_max_age_minutes must be non-negative")


def load_config() -> AppConfig:
    _load_dotenv_files()
    return AppConfig(
        environment=os.getenv("IIMC_ENVIRONMENT", "local"),
        log_level=os.getenv("IIMC_LOG_LEVEL", "INFO"),
        database_path=Path(
            os.getenv("IIMC_DATABASE_PATH", "data/iimc_platform.duckdb")
        ),
        artifacts_dir=Path(os.getenv("IIMC_ARTIFACTS_DIR", "artifacts")),
        strategy_plugin_dir=Path(
            os.getenv("IIMC_STRATEGY_PLUGIN_DIR", "strategy_plugins")
        ),
        openalgo_root=Path(
            os.getenv("OPENALGO_ROOT", str(Path.home() / "openalgo"))
        ),
        openalgo_base_url=os.getenv(
            "OPENALGO_BASE_URL", "http://127.0.0.1:5000"
        ),
        llm_provider=os.getenv("IIMC_LLM_PROVIDER", "groq").strip().lower(),
        groq_api_key=os.getenv("GROQ_API_KEY"),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        groq_fallback_model=(
            os.getenv("GROQ_FALLBACK_MODEL", "llama-3.1-8b-instant").strip()
            or None
        ),
        require_real_llm=os.getenv(
            "IIMC_REQUIRE_REAL_LLM", "false"
        ).lower()
        in {"1", "true", "yes"},
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        openalgo_api_key=os.getenv("OPENALGO_API_KEY"),
        allow_live_trading=os.getenv("IIMC_ALLOW_LIVE_TRADING", "false").lower()
        in {"1", "true", "yes"},
        auth_required=os.getenv("IIMC_AUTH_REQUIRED", "false").lower()
        in {"1", "true", "yes"},
        auth_secret=os.getenv("IIMC_AUTH_SECRET"),
        session_ttl_minutes=int(
            os.getenv("IIMC_SESSION_TTL_MINUTES", "480")
        ),
        allowed_hosts=_csv_tuple(
            os.getenv(
                "IIMC_ALLOWED_HOSTS",
                "127.0.0.1,localhost,testserver",
            )
        ),
        cors_origins=_csv_tuple(os.getenv("IIMC_CORS_ORIGINS", "")),
        rate_limit_per_minute=int(
            os.getenv("IIMC_RATE_LIMIT_PER_MINUTE", "120")
        ),
        max_request_bytes=int(
            os.getenv("IIMC_MAX_REQUEST_BYTES", "1000000")
        ),
        otel_enabled=os.getenv(
            "IIMC_OTEL_ENABLED", "false"
        ).lower()
        in {"1", "true", "yes"},
        otel_service_name=os.getenv(
            "OTEL_SERVICE_NAME", "iimc-trading-platform"
        ),
        otel_exporter_otlp_endpoint=os.getenv(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
        ),
        otel_sample_ratio=float(
            os.getenv("IIMC_OTEL_SAMPLE_RATIO", "1.0")
        ),
        otel_excluded_urls=os.getenv(
            "IIMC_OTEL_EXCLUDED_URLS",
            "live,health,ready,metrics",
        ),
        market_news_provider=os.getenv("MARKET_NEWS_PROVIDER"),
        market_news_api_url=os.getenv("MARKET_NEWS_API_URL"),
        market_news_api_key=os.getenv("MARKET_NEWS_API_KEY"),
        market_news_timeout_seconds=float(
            os.getenv("MARKET_NEWS_TIMEOUT_SECONDS", "10")
        ),
        market_news_max_articles=int(
            os.getenv("MARKET_NEWS_MAX_ARTICLES", "10")
        ),
        require_paper_approval=os.getenv(
            "IIMC_REQUIRE_PAPER_APPROVAL", "false"
        ).lower()
        in {"1", "true", "yes"},
        paper_signal_max_age_minutes=int(
            os.getenv("IIMC_PAPER_SIGNAL_MAX_AGE_MINUTES", "20")
        ),
    )


def public_config(config: AppConfig) -> dict[str, object]:
    return {
        "app_name": config.app_name,
        "environment": config.environment,
        "log_level": config.log_level,
        "openalgo_base_url": config.openalgo_base_url,
        "llm_provider": config.llm_provider,
        "groq_api_key_configured": bool(config.groq_api_key),
        "groq_model": config.groq_model,
        "groq_fallback_model": config.groq_fallback_model,
        "require_real_llm": config.require_real_llm,
        "openai_api_key_configured": bool(config.openai_api_key),
        "openai_model": config.openai_model,
        "openalgo_api_key_configured": bool(config.openalgo_api_key),
        "allow_live_trading": config.allow_live_trading,
        "auth_required": config.auth_required,
        "auth_secret_configured": bool(config.auth_secret),
        "session_ttl_minutes": config.session_ttl_minutes,
        "allowed_hosts": list(config.allowed_hosts),
        "cors_origins": list(config.cors_origins),
        "rate_limit_per_minute": config.rate_limit_per_minute,
        "max_request_bytes": config.max_request_bytes,
        "otel_enabled": config.otel_enabled,
        "otel_service_name": config.otel_service_name,
        "otel_exporter_configured": bool(
            config.otel_exporter_otlp_endpoint
        ),
        "otel_sample_ratio": config.otel_sample_ratio,
        "market_news_provider_configured": bool(
            config.market_news_provider
        ),
        "market_news_api_url_configured": bool(config.market_news_api_url),
        "market_news_api_key_configured": bool(config.market_news_api_key),
        "market_news_max_articles": config.market_news_max_articles,
        "require_paper_approval": config.require_paper_approval,
        "paper_signal_max_age_minutes": config.paper_signal_max_age_minutes,
    }


def _load_dotenv_files() -> None:
    for path in _candidate_env_paths():
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name and name not in os.environ:
                os.environ[name] = value


def _candidate_env_paths() -> tuple[Path, ...]:
    cwd = Path.cwd()
    package_root = Path(__file__).resolve().parents[1]
    return (
        cwd / ".env",
        cwd / ".env.local",
        package_root / ".env",
        package_root / ".env.local",
    )


def _csv_tuple(value: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in value.split(",")
        if item.strip()
    )
