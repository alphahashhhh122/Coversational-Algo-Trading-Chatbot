from __future__ import annotations

import json
from pathlib import Path

from ..db import connect


CORE_TABLES = [
    "raw_file_registry",
    "options_ohlcv",
    "market_ohlcv",
    "data_catalog",
    "data_quality_reports",
    "strategy_runs",
    "strategy_signals",
    "risk_decisions",
    "order_events",
    "order_state_events",
    "trade_fills",
    "performance_summaries",
    "experiment_manifests",
    "robustness_experiments",
    "robustness_trials",
    "portfolios",
    "portfolio_positions",
    "portfolio_ledger",
    "portfolio_risk_decisions",
    "risk_reservations",
    "risk_control_state",
    "work_tasks",
    "schema_versions",
    "audit_events",
    "chat_sessions",
    "chat_messages",
    "tool_calls",
    "report_artifacts",
    "openalgo_snapshots",
    "strategy_definitions",
    "strategy_personas",
    "custom_strategy_specs",
    "risk_limits",
    "approval_requests",
    "order_intents",
    "dataset_freshness_policies",
    "freshness_assessments",
    "knowledge_documents",
    "knowledge_chunks",
    "retrieval_events",
    "ai_eval_runs",
    "ai_eval_results",
    "retrieval_eval_runs",
    "retrieval_eval_results",
    "retention_policies",
    "retention_runs",
    "alert_rules",
    "operational_alerts",
    "alert_evaluation_runs",
    "backup_verifications",
    "app_users",
    "auth_sessions",
    "scheduled_jobs",
    "job_runs",
    "market_news_fetches",
    "market_news_articles",
    "dashboard_preferences",
    "research_briefs",
]


def initialize_database(db_path: Path) -> None:
    con = connect(db_path)
    try:
        con.execute("BEGIN TRANSACTION")
        _rename_legacy_table_if_needed(
            con,
            table_name="strategy_runs",
            required_column="strategy_id",
            legacy_table_name="legacy_ema_strategy_runs",
        )
        _rename_legacy_table_if_needed(
            con,
            table_name="strategy_signals",
            required_column="direction",
            legacy_table_name="legacy_ema_strategy_signals",
        )
        _rename_legacy_table_if_needed(
            con,
            table_name="risk_decisions",
            required_column="risk_policy_version",
            legacy_table_name="legacy_ema_risk_decisions",
        )
        _rename_legacy_table_if_needed(
            con,
            table_name="performance_summaries",
            required_column="metrics_json",
            legacy_table_name="legacy_ema_performance_summaries",
        )
        _rename_table_if_exists(
            con,
            table_name="simulated_orders",
            legacy_table_name="legacy_ema_simulated_orders",
        )
        _rename_table_if_exists(
            con,
            table_name="simulated_trades",
            legacy_table_name="legacy_ema_simulated_trades",
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_file_registry (
                source_id VARCHAR PRIMARY KEY,
                source_path VARCHAR NOT NULL,
                source_name VARCHAR NOT NULL,
                sha256 VARCHAR NOT NULL,
                byte_size BIGINT NOT NULL,
                ingested_at TIMESTAMP NOT NULL,
                row_count BIGINT NOT NULL,
                valid_row_count BIGINT NOT NULL,
                duplicate_row_count BIGINT NOT NULL,
                invalid_row_count BIGINT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS options_ohlcv (
                underlying VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                expiry VARCHAR NOT NULL,
                interval VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                strike_label VARCHAR NOT NULL,
                strike_price DOUBLE NOT NULL,
                option_type VARCHAR NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume BIGINT NOT NULL,
                oi BIGINT NOT NULL,
                iv DOUBLE,
                spot DOUBLE,
                source_id VARCHAR NOT NULL,
                source_file VARCHAR NOT NULL,
                quality_status VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY (
                    underlying,
                    exchange,
                    expiry,
                    interval,
                    timestamp,
                    strike_price,
                    option_type
                )
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS market_ohlcv (
                source_id VARCHAR NOT NULL,
                asset_class VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                interval VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume DOUBLE NOT NULL,
                quality_status VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY (source_id, timestamp)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS market_features (
                source_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                feature_name VARCHAR NOT NULL,
                observed_at TIMESTAMP NOT NULL,
                available_at TIMESTAMP NOT NULL,
                value DOUBLE NOT NULL,
                metadata_json VARCHAR NOT NULL,
                quality_status VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY (
                    source_id,
                    feature_name,
                    observed_at,
                    available_at
                )
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS data_catalog (
                dataset_id VARCHAR PRIMARY KEY,
                data_domain VARCHAR NOT NULL,
                data_type VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                interval VARCHAR,
                start_ts TIMESTAMP,
                end_ts TIMESTAMP,
                row_count BIGINT NOT NULL,
                storage_table VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                quality_status VARCHAR NOT NULL,
                quality_report_path VARCHAR,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS data_quality_reports (
                run_id VARCHAR PRIMARY KEY,
                source_id VARCHAR NOT NULL,
                dataset_id VARCHAR NOT NULL,
                report_path VARCHAR NOT NULL,
                total_rows BIGINT NOT NULL,
                valid_rows BIGINT NOT NULL,
                inserted_rows BIGINT NOT NULL,
                duplicate_rows BIGINT NOT NULL,
                invalid_rows BIGINT NOT NULL,
                pair_gap_count BIGINT NOT NULL,
                quality_status VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_runs (
                run_id VARCHAR PRIMARY KEY,
                strategy_id VARCHAR NOT NULL,
                dataset_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                execution_mode VARCHAR NOT NULL,
                parameters_json VARCHAR NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                error_message VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_signals (
                signal_id VARCHAR PRIMARY KEY,
                run_id VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                symbol VARCHAR NOT NULL,
                signal_type VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                confidence DOUBLE,
                reason VARCHAR NOT NULL,
                features_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_decisions (
                decision_id VARCHAR PRIMARY KEY,
                run_id VARCHAR NOT NULL,
                signal_id VARCHAR NOT NULL,
                approved BOOLEAN NOT NULL,
                requested_quantity BIGINT NOT NULL,
                approved_quantity BIGINT NOT NULL,
                reason VARCHAR NOT NULL,
                checks_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                risk_policy_version VARCHAR NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS order_events (
                order_id VARCHAR PRIMARY KEY,
                run_id VARCHAR NOT NULL,
                decision_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                order_type VARCHAR NOT NULL,
                quantity BIGINT NOT NULL,
                status VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL,
                broker_order_id VARCHAR,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP
            )
            """
        )
        for column_name, column_definition in [
            ("execution_mode", "execution_mode VARCHAR"),
            ("price", "price DOUBLE"),
            ("idempotency_key", "idempotency_key VARCHAR"),
            ("filled_quantity", "filled_quantity BIGINT DEFAULT 0"),
            ("average_fill_price", "average_fill_price DOUBLE"),
            ("rejection_reason", "rejection_reason VARCHAR"),
        ]:
            _add_column_if_missing(
                con,
                table_name="order_events",
                column_name=column_name,
                column_definition=column_definition,
            )
        con.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS order_events_idempotency_idx
            ON order_events(idempotency_key)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS order_state_events (
                event_id VARCHAR PRIMARY KEY,
                order_id VARCHAR NOT NULL,
                from_status VARCHAR,
                to_status VARCHAR NOT NULL,
                reason VARCHAR,
                payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_fills (
                trade_id VARCHAR PRIMARY KEY,
                order_id VARCHAR NOT NULL,
                run_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                quantity BIGINT NOT NULL,
                price DOUBLE NOT NULL,
                fees DOUBLE NOT NULL,
                realized_pnl DOUBLE NOT NULL,
                filled_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS performance_summaries (
                run_id VARCHAR PRIMARY KEY,
                total_trades BIGINT NOT NULL,
                net_pnl DOUBLE NOT NULL,
                max_drawdown DOUBLE NOT NULL,
                return_pct DOUBLE NOT NULL,
                metrics_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS experiment_manifests (
                manifest_id VARCHAR PRIMARY KEY,
                run_id VARCHAR NOT NULL UNIQUE,
                strategy_name VARCHAR NOT NULL,
                strategy_version VARCHAR NOT NULL,
                dataset_id VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                source_sha256 VARCHAR NOT NULL,
                parameters_json VARCHAR NOT NULL,
                engine_version VARCHAR NOT NULL,
                manifest_sha256 VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS robustness_experiments (
                experiment_id VARCHAR PRIMARY KEY,
                strategy_name VARCHAR NOT NULL,
                dataset_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                split_ratio DOUBLE NOT NULL,
                train_start TIMESTAMP NOT NULL,
                train_end TIMESTAMP NOT NULL,
                test_start TIMESTAMP NOT NULL,
                test_end TIMESTAMP NOT NULL,
                candidate_count BIGINT NOT NULL,
                selected_parameters_json VARCHAR,
                selected_train_run_id VARCHAR,
                selected_test_run_id VARCHAR,
                benchmark_json VARCHAR,
                verdict VARCHAR,
                evaluation_policy_version VARCHAR DEFAULT '1.1.0',
                summary_json VARCHAR,
                requested_by VARCHAR NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                error_message VARCHAR
            )
            """
        )
        _add_column_if_missing(
            con,
            table_name="robustness_experiments",
            column_name="evaluation_policy_version",
            column_definition=(
                "evaluation_policy_version VARCHAR DEFAULT '1.1.0'"
            ),
        )
        con.execute(
            """
            UPDATE robustness_experiments
            SET evaluation_policy_version = COALESCE(
                evaluation_policy_version,
                '1.1.0'
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS robustness_trials (
                trial_id VARCHAR PRIMARY KEY,
                experiment_id VARCHAR NOT NULL,
                candidate_index BIGINT NOT NULL,
                parameters_json VARCHAR NOT NULL,
                train_metrics_json VARCHAR NOT NULL,
                test_metrics_json VARCHAR NOT NULL,
                train_score DOUBLE NOT NULL,
                selected BOOLEAN NOT NULL,
                created_at TIMESTAMP NOT NULL,
                UNIQUE(experiment_id, candidate_index)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolios (
                portfolio_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL UNIQUE,
                base_currency VARCHAR NOT NULL,
                starting_cash DOUBLE NOT NULL,
                cash_balance DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_positions (
                portfolio_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                quantity BIGINT NOT NULL,
                average_price DOUBLE NOT NULL,
                last_price DOUBLE NOT NULL,
                realized_pnl DOUBLE NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (portfolio_id, symbol)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_ledger (
                ledger_entry_id VARCHAR PRIMARY KEY,
                portfolio_id VARCHAR NOT NULL,
                event_type VARCHAR NOT NULL,
                reference_id VARCHAR NOT NULL,
                symbol VARCHAR,
                side VARCHAR,
                quantity BIGINT,
                price DOUBLE,
                fees DOUBLE NOT NULL,
                cash_delta DOUBLE NOT NULL,
                realized_pnl DOUBLE NOT NULL,
                payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                UNIQUE(portfolio_id, event_type, reference_id)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_risk_decisions (
                decision_id VARCHAR PRIMARY KEY,
                portfolio_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                requested_quantity BIGINT NOT NULL,
                approved_quantity BIGINT NOT NULL,
                price DOUBLE NOT NULL,
                approved BOOLEAN NOT NULL,
                reason VARCHAR NOT NULL,
                checks_json VARCHAR NOT NULL,
                policy_version VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_reservations (
                reservation_id VARCHAR PRIMARY KEY,
                decision_id VARCHAR NOT NULL UNIQUE,
                portfolio_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                quantity BIGINT NOT NULL,
                notional DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_control_state (
                scope VARCHAR NOT NULL,
                scope_id VARCHAR NOT NULL,
                trading_enabled BOOLEAN NOT NULL,
                reason VARCHAR,
                changed_by VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (scope, scope_id)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS work_tasks (
                task_id VARCHAR PRIMARY KEY,
                task_type VARCHAR NOT NULL,
                idempotency_key VARCHAR UNIQUE,
                payload_json VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                result_json VARCHAR,
                error_type VARCHAR,
                error_message VARCHAR,
                requested_by VARCHAR NOT NULL,
                attempt BIGINT NOT NULL,
                max_attempts BIGINT NOT NULL,
                next_attempt_at TIMESTAMP NOT NULL,
                locked_at TIMESTAMP,
                locked_by VARCHAR,
                created_at TIMESTAMP NOT NULL,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_versions (
                version_id VARCHAR PRIMARY KEY,
                description VARCHAR NOT NULL,
                applied_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'experiment_reproducibility_v1',
                'Immutable experiment manifests and reproducibility hashes',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'strategy_robustness_v1',
                'Chronological out-of-sample and parameter sensitivity evidence',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'strategy_robustness_policy_v1_1',
                'Version robustness verdict rules and minimum test sample',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'portfolio_risk_v1',
                'Durable portfolio ledger, positions, reservations, and kill switch',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'durable_work_tasks_v1',
                'One-off durable task queue with claims, retries, and results',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'week1_foundation_v1',
                'Week 1 production foundation schema',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'platform_runtime_v1',
                'Generic strategy, risk, and idempotent order runtime',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'week1_foundation_v2',
                'Centralized schema and complete data-domain vocabulary',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                audit_id VARCHAR PRIMARY KEY,
                entity_type VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                actor VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id VARCHAR PRIMARY KEY,
                title VARCHAR,
                user_id VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'active',
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        for column_name, column_definition in [
            ("title", "title VARCHAR"),
            ("user_id", "user_id VARCHAR"),
            ("status", "status VARCHAR DEFAULT 'active'"),
            ("created_at", "created_at TIMESTAMP"),
            ("updated_at", "updated_at TIMESTAMP"),
        ]:
            _add_column_if_missing(
                con,
                table_name="chat_sessions",
                column_name=column_name,
                column_definition=column_definition,
            )
        con.execute(
            """
            UPDATE chat_sessions
            SET status = COALESCE(status, 'active'),
                created_at = COALESCE(created_at, CURRENT_TIMESTAMP),
                updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                message_id VARCHAR PRIMARY KEY,
                session_id VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                content VARCHAR NOT NULL,
                metadata_json VARCHAR NOT NULL DEFAULT '{}',
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        for column_name, column_definition in [
            ("metadata_json", "metadata_json VARCHAR DEFAULT '{}'"),
            ("created_at", "created_at TIMESTAMP"),
        ]:
            _add_column_if_missing(
                con,
                table_name="chat_messages",
                column_name=column_name,
                column_definition=column_definition,
            )
        con.execute(
            """
            UPDATE chat_messages
            SET metadata_json = COALESCE(metadata_json, '{}'),
                created_at = COALESCE(created_at, CURRENT_TIMESTAMP)
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_calls (
                tool_call_id VARCHAR PRIMARY KEY,
                session_id VARCHAR,
                tool_name VARCHAR NOT NULL,
                request_json VARCHAR NOT NULL,
                response_json VARCHAR,
                status VARCHAR NOT NULL,
                error_message VARCHAR,
                created_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                trace_id VARCHAR,
                span_id VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS dashboard_preferences (
                principal_id VARCHAR PRIMARY KEY,
                widgets_json VARCHAR NOT NULL,
                auto_refresh BOOLEAN NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS research_briefs (
                brief_id VARCHAR PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                asset_class VARCHAR NOT NULL,
                interval VARCHAR NOT NULL,
                start_date VARCHAR NOT NULL,
                end_date VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        _add_column_if_missing(
            con,
            table_name="tool_calls",
            column_name="error_message",
            column_definition="error_message VARCHAR",
        )
        _add_column_if_missing(
            con,
            table_name="tool_calls",
            column_name="trace_id",
            column_definition="trace_id VARCHAR",
        )
        _add_column_if_missing(
            con,
            table_name="tool_calls",
            column_name="span_id",
            column_definition="span_id VARCHAR",
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'week1_foundation_v3',
                'Add error message field to tool call lifecycle records',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'tool_call_trace_context_v1',
                'Persist OpenTelemetry trace and span identifiers',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS report_artifacts (
                report_id VARCHAR PRIMARY KEY,
                report_type VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                path VARCHAR NOT NULL,
                source_run_id VARCHAR,
                metadata_json VARCHAR NOT NULL DEFAULT '{}',
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        _add_column_if_missing(
            con,
            table_name="report_artifacts",
            column_name="metadata_json",
            column_definition="metadata_json VARCHAR DEFAULT '{}'",
        )
        con.execute(
            """
            UPDATE report_artifacts
            SET metadata_json = COALESCE(metadata_json, '{}')
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'report_artifacts_v2',
                'Add structured metadata to persisted report artifacts',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS openalgo_snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                snapshot_type VARCHAR NOT NULL,
                source_table VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL,
                captured_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS market_news_fetches (
                fetch_id VARCHAR PRIMARY KEY,
                provider VARCHAR NOT NULL,
                query VARCHAR,
                symbol VARCHAR,
                raw_artifact_path VARCHAR NOT NULL,
                article_count BIGINT NOT NULL,
                status VARCHAR NOT NULL,
                error_message VARCHAR,
                retrieved_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS market_news_articles (
                article_id VARCHAR PRIMARY KEY,
                fetch_id VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                source_url VARCHAR,
                published_at TIMESTAMP,
                retrieved_at TIMESTAMP NOT NULL,
                provider VARCHAR NOT NULL,
                query VARCHAR,
                symbol VARCHAR,
                sha256 VARCHAR NOT NULL,
                raw_json VARCHAR NOT NULL,
                UNIQUE(sha256)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist_symbols (
                symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                added_by VARCHAR NOT NULL,
                added_at TIMESTAMP NOT NULL,
                PRIMARY KEY (symbol, exchange)
            )
            """
        )
        # --- ATL agent platform (see docs/ATL_TRANSITION.md) -----------------
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                agent_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                version VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                description VARCHAR NOT NULL,
                capabilities_json VARCHAR NOT NULL,
                config_json VARCHAR NOT NULL,
                author VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                UNIQUE(name, version)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id VARCHAR PRIMARY KEY,
                agent_id VARCHAR NOT NULL,
                version VARCHAR NOT NULL,
                task_json VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                findings_json VARCHAR,
                evidence_json VARCHAR,
                gaps_json VARCHAR,
                cost_json VARCHAR,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_scores (
                score_id VARCHAR PRIMARY KEY,
                agent_id VARCHAR NOT NULL,
                version VARCHAR NOT NULL,
                run_id VARCHAR NOT NULL,
                eval_dataset_id VARCHAR,
                metrics_json VARCHAR NOT NULL,
                composite DOUBLE,
                scored_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_datasets (
                eval_dataset_id VARCHAR PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                bar_interval VARCHAR NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                row_count INTEGER NOT NULL,
                content_hash VARCHAR NOT NULL,
                frozen_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS universe_backfill_status (
                symbol VARCHAR NOT NULL,
                interval VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                rows_imported INTEGER NOT NULL,
                reason VARCHAR,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (symbol, interval)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS supervisor_findings (
                finding_id VARCHAR PRIMARY KEY,
                kind VARCHAR NOT NULL,
                severity VARCHAR NOT NULL,
                agent_id VARCHAR,
                summary VARCHAR NOT NULL,
                detail_json VARCHAR NOT NULL,
                acknowledged BOOLEAN NOT NULL,
                detected_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_digests (
                digest_id VARCHAR PRIMARY KEY,
                headline VARCHAR NOT NULL,
                sections_json VARCHAR NOT NULL,
                generated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS contests (
                contest_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                eval_dataset_id VARCHAR,
                dataset_hash VARCHAR,
                closes_at TIMESTAMP NOT NULL,
                status VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS contest_results (
                result_id VARCHAR PRIMARY KEY,
                contest_id VARCHAR NOT NULL,
                agent_id VARCHAR NOT NULL,
                version VARCHAR NOT NULL,
                run_id VARCHAR,
                metrics_json VARCHAR NOT NULL,
                composite DOUBLE,
                rank INTEGER,
                snapshot_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS arena_seasons (
                season_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                starting_equity DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                started_at TIMESTAMP NOT NULL,
                ended_at TIMESTAMP,
                symbols_json VARCHAR
            )
            """
        )
        # Existing databases predate basket seasons; add the column in place.
        _add_column_if_missing(
            con,
            table_name="arena_seasons",
            column_name="symbols_json",
            column_definition="symbols_json VARCHAR",
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS arena_entries (
                entry_id VARCHAR PRIMARY KEY,
                season_id VARCHAR NOT NULL,
                agent_id VARCHAR NOT NULL,
                parameters_json VARCHAR NOT NULL,
                strategy_name VARCHAR NOT NULL,
                enrolled_at TIMESTAMP NOT NULL,
                UNIQUE(season_id, agent_id)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS arena_equity_daily (
                snapshot_id VARCHAR PRIMARY KEY,
                season_id VARCHAR NOT NULL,
                entry_id VARCHAR NOT NULL,
                as_of DATE NOT NULL,
                equity DOUBLE,
                return_pct DOUBLE,
                trades INTEGER,
                max_drawdown DOUBLE,
                data_status VARCHAR NOT NULL,
                recorded_at TIMESTAMP NOT NULL,
                UNIQUE(entry_id, as_of)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_memory (
                memory_id VARCHAR PRIMARY KEY,
                kind VARCHAR NOT NULL,
                memory_key VARCHAR NOT NULL,
                content VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                UNIQUE(kind, memory_key)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS technical_watches (
                watch_id VARCHAR PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                condition VARCHAR NOT NULL,
                threshold DOUBLE,
                status VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                last_value DOUBLE,
                last_checked_at TIMESTAMP,
                triggered_at TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS price_alerts (
                alert_id VARCHAR PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                threshold DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                last_price DOUBLE,
                last_checked_at TIMESTAMP,
                triggered_at TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS screen_definitions (
                screen_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                description VARCHAR NOT NULL,
                criteria_json VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                UNIQUE(name, version)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS financial_statements (
                statement_id VARCHAR PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                period VARCHAR NOT NULL,
                period_end DATE NOT NULL,
                currency VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                revenue DOUBLE,
                operating_profit DOUBLE,
                net_income DOUBLE,
                total_assets DOUBLE,
                total_equity DOUBLE,
                total_debt DOUBLE,
                current_assets DOUBLE,
                current_liabilities DOUBLE,
                operating_cash_flow DOUBLE,
                capital_expenditure DOUBLE,
                shares_outstanding DOUBLE,
                dividends_paid DOUBLE,
                imported_by VARCHAR NOT NULL,
                imported_at TIMESTAMP NOT NULL,
                UNIQUE(symbol, period, period_end)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_definitions (
                strategy_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                version VARCHAR NOT NULL,
                description VARCHAR NOT NULL,
                parameter_schema_json VARCHAR NOT NULL,
                enabled BOOLEAN NOT NULL,
                created_at TIMESTAMP NOT NULL,
                UNIQUE(name, version)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_personas (
                persona_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL UNIQUE,
                description VARCHAR NOT NULL,
                asset_classes_json VARCHAR NOT NULL,
                strategy_bias_json VARCHAR NOT NULL,
                risk_rules_json VARCHAR NOT NULL,
                dashboard_focus_json VARCHAR NOT NULL,
                prompt_guidance VARCHAR NOT NULL,
                enabled BOOLEAN NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_strategy_specs (
                spec_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                description VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                spec_json VARCHAR NOT NULL,
                validation_json VARCHAR NOT NULL,
                missing_capabilities_json VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.executemany(
            """
            INSERT OR IGNORE INTO strategy_personas VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            [
                (
                    "balanced_systematic",
                    "Balanced Systematic",
                    "Rules-first profile for general research, backtesting, and paper execution.",
                    json.dumps(
                        [
                            "equity",
                            "index",
                            "futures",
                            "options",
                            "commodity",
                            "crypto",
                        ]
                    ),
                    json.dumps(
                        {
                            "preferred_strategies": [
                                "ema_crossover",
                                "sma_crossover",
                                "momentum_roc",
                            ],
                            "selection_style": "prefer liquid symbols and avoid unsupported contracts",
                        }
                    ),
                    json.dumps(
                        {
                            "max_order_value": 250000,
                            "requires_approval_for_paper": True,
                            "requires_approval_for_live": True,
                            "avoid_overrides": True,
                        }
                    ),
                    json.dumps(
                        [
                            "net_pnl",
                            "max_drawdown",
                            "win_rate",
                            "orders",
                            "risk_decisions",
                        ]
                    ),
                    "Use systematic evidence, cite data freshness, and keep execution conservative unless the user asks for a different profile.",
                ),
                (
                    "conservative_value",
                    "Conservative Value",
                    "Longer-horizon profile inspired by quality, valuation, and capital-preservation constraints.",
                    json.dumps(["equity", "index"]),
                    json.dumps(
                        {
                            "preferred_strategies": [
                                "sma_crossover",
                                "rsi_mean_reversion",
                            ],
                            "selection_style": "prefer defensible large-cap or index exposure over short-term speculation",
                        }
                    ),
                    json.dumps(
                        {
                            "max_order_value": 100000,
                            "stop_loss_pct": 0.02,
                            "requires_approval_for_paper": True,
                            "requires_approval_for_live": True,
                        }
                    ),
                    json.dumps(
                        [
                            "drawdown",
                            "capital_at_risk",
                            "holding_period",
                            "downside_notes",
                        ]
                    ),
                    "Explain tradeoffs in value-investor language, but do not claim to imitate any real investor or bypass platform risk checks.",
                ),
                (
                    "intraday_momentum",
                    "Intraday Momentum",
                    "Short-horizon profile for liquid instruments, momentum signals, and fast risk feedback.",
                    json.dumps(
                        [
                            "equity",
                            "index",
                            "futures",
                            "options",
                            "commodity",
                            "crypto",
                        ]
                    ),
                    json.dumps(
                        {
                            "preferred_strategies": [
                                "ema_crossover",
                                "momentum_roc",
                            ],
                            "selection_style": "prefer fresh intraday data, liquidity, and explicit exits",
                        }
                    ),
                    json.dumps(
                        {
                            "max_order_value": 150000,
                            "stop_loss_pct": 0.01,
                            "requires_approval_for_paper": True,
                            "requires_approval_for_live": True,
                        }
                    ),
                    json.dumps(
                        [
                            "latency",
                            "slippage",
                            "entry_signal",
                            "exit_signal",
                            "open_positions",
                        ]
                    ),
                    "Prioritize data freshness, execution readiness, and slippage-aware wording for intraday workflows.",
                ),
                (
                    "risk_off_capital_preservation",
                    "Risk-Off Capital Preservation",
                    "Defensive profile that emphasizes avoiding trades when data, broker, or risk evidence is incomplete.",
                    json.dumps(
                        [
                            "equity",
                            "index",
                            "futures",
                            "options",
                            "commodity",
                            "crypto",
                        ]
                    ),
                    json.dumps(
                        {
                            "preferred_strategies": [
                                "rsi_mean_reversion",
                                "sma_crossover",
                            ],
                            "selection_style": "prefer no-trade decisions when evidence is incomplete",
                        }
                    ),
                    json.dumps(
                        {
                            "max_order_value": 50000,
                            "stop_loss_pct": 0.005,
                            "requires_approval_for_paper": True,
                            "requires_approval_for_live": True,
                        }
                    ),
                    json.dumps(
                        [
                            "blocked_stages",
                            "risk_rejections",
                            "freshness",
                            "cash",
                            "kill_switch",
                        ]
                    ),
                    "Make uncertainty explicit and recommend readiness checks before any execution workflow.",
                ),
            ],
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_limits (
                risk_limit_id VARCHAR PRIMARY KEY,
                scope VARCHAR NOT NULL,
                scope_id VARCHAR NOT NULL,
                execution_mode VARCHAR NOT NULL,
                max_order_value DOUBLE,
                max_position_value DOUBLE,
                max_loss_per_trade DOUBLE,
                max_daily_loss DOUBLE,
                requires_approval BOOLEAN NOT NULL,
                enabled BOOLEAN NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        for column_name, column_definition in [
            ("policy_version", "policy_version VARCHAR"),
            ("max_quantity", "max_quantity BIGINT"),
            ("stop_loss_pct", "stop_loss_pct DOUBLE"),
            ("min_confidence", "min_confidence DOUBLE"),
            ("allowed_symbols_json", "allowed_symbols_json VARCHAR"),
        ]:
            _add_column_if_missing(
                con,
                table_name="risk_limits",
                column_name=column_name,
                column_definition=column_definition,
            )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_requests (
                approval_id VARCHAR PRIMARY KEY,
                entity_type VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                requested_action VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                requested_by VARCHAR NOT NULL,
                decided_by VARCHAR,
                reason VARCHAR,
                created_at TIMESTAMP NOT NULL,
                decided_at TIMESTAMP
            )
            """
        )
        _add_column_if_missing(
            con,
            table_name="approval_requests",
            column_name="metadata_json",
            column_definition="metadata_json VARCHAR DEFAULT '{}'",
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS order_intents (
                intent_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                signal_id VARCHAR,
                symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                product VARCHAR NOT NULL,
                order_type VARCHAR NOT NULL,
                quantity BIGINT NOT NULL,
                limit_price DOUBLE,
                execution_mode VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        for column_name, column_definition in [
            ("decision_id", "decision_id VARCHAR"),
            ("approval_id", "approval_id VARCHAR"),
            ("order_id", "order_id VARCHAR"),
            ("broker_order_id", "broker_order_id VARCHAR"),
            ("idempotency_key", "idempotency_key VARCHAR"),
            ("rejection_reason", "rejection_reason VARCHAR"),
            ("strategy_name", "strategy_name VARCHAR"),
            ("trigger_price", "trigger_price DOUBLE"),
        ]:
            _add_column_if_missing(
                con,
                table_name="order_intents",
                column_name=column_name,
                column_definition=column_definition,
            )
        con.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS order_intents_idempotency_idx
            ON order_intents(idempotency_key)
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'openalgo_sandbox_bridge_v1',
                'Approval-gated OpenAlgo analyzer submission and reconciliation',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS dataset_freshness_policies (
                policy_id VARCHAR PRIMARY KEY,
                dataset_id VARCHAR NOT NULL,
                purpose VARCHAR NOT NULL,
                max_age_seconds BIGINT,
                future_tolerance_seconds BIGINT NOT NULL,
                accepted_quality_json VARCHAR NOT NULL,
                enabled BOOLEAN NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                UNIQUE(dataset_id, purpose)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS freshness_assessments (
                assessment_id VARCHAR PRIMARY KEY,
                dataset_id VARCHAR NOT NULL,
                purpose VARCHAR NOT NULL,
                reference_time TIMESTAMP NOT NULL,
                dataset_end_ts TIMESTAMP,
                age_seconds BIGINT,
                status VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                policy_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                document_id VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL,
                source_uri VARCHAR NOT NULL,
                sha256 VARCHAR NOT NULL UNIQUE,
                document_type VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                metadata_json VARCHAR NOT NULL,
                ingested_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                chunk_id VARCHAR PRIMARY KEY,
                document_id VARCHAR NOT NULL,
                chunk_index BIGINT NOT NULL,
                content VARCHAR NOT NULL,
                token_count BIGINT NOT NULL,
                metadata_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                UNIQUE(document_id, chunk_index)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_events (
                retrieval_id VARCHAR PRIMARY KEY,
                session_id VARCHAR,
                query VARCHAR NOT NULL,
                document_ids_json VARCHAR NOT NULL,
                chunk_ids_json VARCHAR NOT NULL,
                method VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_eval_runs (
                eval_run_id VARCHAR PRIMARY KEY,
                case_set_sha256 VARCHAR NOT NULL,
                case_count BIGINT NOT NULL,
                passed_count BIGINT NOT NULL,
                failed_count BIGINT NOT NULL,
                pass_rate DOUBLE NOT NULL,
                orchestration_mode VARCHAR NOT NULL,
                model VARCHAR,
                status VARCHAR NOT NULL,
                artifact_path VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_eval_results (
                eval_run_id VARCHAR NOT NULL,
                case_id VARCHAR NOT NULL,
                case_type VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                role VARCHAR,
                passed BOOLEAN NOT NULL,
                expected_json VARCHAR NOT NULL,
                actual_json VARCHAR NOT NULL,
                error_message VARCHAR,
                duration_ms DOUBLE NOT NULL,
                PRIMARY KEY (eval_run_id, case_id)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_eval_runs (
                eval_run_id VARCHAR PRIMARY KEY,
                case_set_sha256 VARCHAR NOT NULL,
                corpus_sha256 VARCHAR NOT NULL,
                retrieval_method VARCHAR NOT NULL,
                case_count BIGINT NOT NULL,
                recall_at_k DOUBLE NOT NULL,
                mean_reciprocal_rank DOUBLE NOT NULL,
                ndcg_at_k DOUBLE NOT NULL,
                top_k BIGINT NOT NULL,
                status VARCHAR NOT NULL,
                artifact_path VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_eval_results (
                eval_run_id VARCHAR NOT NULL,
                case_id VARCHAR NOT NULL,
                query VARCHAR NOT NULL,
                relevant_titles_json VARCHAR NOT NULL,
                retrieved_titles_json VARCHAR NOT NULL,
                first_relevant_rank BIGINT,
                recall_at_k DOUBLE NOT NULL,
                reciprocal_rank DOUBLE NOT NULL,
                ndcg_at_k DOUBLE NOT NULL,
                passed BOOLEAN NOT NULL,
                duration_ms DOUBLE NOT NULL,
                PRIMARY KEY (eval_run_id, case_id)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS retention_policies (
                policy_name VARCHAR PRIMARY KEY,
                retention_days BIGINT NOT NULL,
                enabled BOOLEAN NOT NULL,
                automatic_execution BOOLEAN NOT NULL,
                description VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS retention_runs (
                retention_run_id VARCHAR PRIMARY KEY,
                mode VARCHAR NOT NULL,
                policy_names_json VARCHAR NOT NULL,
                candidate_counts_json VARCHAR NOT NULL,
                deleted_counts_json VARCHAR NOT NULL,
                actor VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_rules (
                rule_key VARCHAR PRIMARY KEY,
                severity VARCHAR NOT NULL,
                threshold_value DOUBLE NOT NULL,
                enabled BOOLEAN NOT NULL,
                description VARCHAR NOT NULL,
                runbook_uri VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS operational_alerts (
                alert_id VARCHAR PRIMARY KEY,
                rule_key VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                severity VARCHAR NOT NULL,
                observed_value DOUBLE NOT NULL,
                threshold_value DOUBLE NOT NULL,
                message VARCHAR NOT NULL,
                details_json VARCHAR NOT NULL,
                first_seen_at TIMESTAMP NOT NULL,
                last_seen_at TIMESTAMP NOT NULL,
                acknowledged_by VARCHAR,
                acknowledged_at TIMESTAMP,
                acknowledgement_reason VARCHAR,
                resolved_at TIMESTAMP
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_evaluation_runs (
                evaluation_id VARCHAR PRIMARY KEY,
                observed_json VARCHAR NOT NULL,
                active_count BIGINT NOT NULL,
                acknowledged_count BIGINT NOT NULL,
                resolved_count BIGINT NOT NULL,
                status VARCHAR NOT NULL,
                actor VARCHAR NOT NULL,
                evaluated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS backup_verifications (
                verification_id VARCHAR PRIMARY KEY,
                backup_id VARCHAR NOT NULL,
                archive_sha256 VARCHAR NOT NULL,
                table_count BIGINT NOT NULL,
                row_count BIGINT NOT NULL,
                status VARCHAR NOT NULL,
                verified_by VARCHAR NOT NULL,
                verified_at TIMESTAMP NOT NULL
            )
            """
        )
        con.executemany(
            """
            INSERT OR IGNORE INTO retention_policies VALUES (
                ?, ?, TRUE, FALSE, ?, CURRENT_TIMESTAMP
            )
            """,
            [
                (
                    "expired_auth_sessions",
                    30,
                    "Expired or revoked authentication sessions",
                ),
                (
                    "inactive_chat_history",
                    180,
                    "Inactive chat sessions and their messages",
                ),
                (
                    "completed_tool_calls",
                    365,
                    "Completed or failed tool-call payloads",
                ),
                (
                    "openalgo_snapshots",
                    90,
                    "Historical broker account snapshots",
                ),
                (
                    "retrieval_events",
                    180,
                    "Knowledge retrieval query audit events",
                ),
                (
                    "job_runs",
                    90,
                    "Completed scheduled-job execution records",
                ),
                (
                    "terminal_work_tasks",
                    90,
                    "Succeeded or failed durable task records",
                ),
            ],
        )
        con.executemany(
            """
            INSERT OR IGNORE INTO alert_rules VALUES (
                ?, ?, ?, TRUE, ?, ?, CURRENT_TIMESTAMP
            )
            """,
            [
                (
                    "uncertain_broker_submissions",
                    "critical",
                    0.0,
                    "Order submissions requiring broker reconciliation",
                    "docs/OPERATIONS_FAILURE_RUNBOOK.md#uncertain-broker-submissions",
                ),
                (
                    "stale_running_tasks",
                    "critical",
                    0.0,
                    "Durable tasks with expired worker leases",
                    "docs/OPERATIONS_FAILURE_RUNBOOK.md#stale-running-tasks",
                ),
                (
                    "failed_tasks_24h",
                    "warning",
                    0.0,
                    "Terminal durable-task failures in the last 24 hours",
                    "docs/OPERATIONS_FAILURE_RUNBOOK.md#failed-tasks",
                ),
                (
                    "failed_jobs_24h",
                    "warning",
                    0.0,
                    "Scheduled-job failures in the last 24 hours",
                    "docs/OPERATIONS_FAILURE_RUNBOOK.md#failed-jobs",
                ),
                (
                    "stale_current_market_datasets",
                    "warning",
                    0.0,
                    "Latest current-market freshness assessments are stale",
                    "docs/OPERATIONS_FAILURE_RUNBOOK.md#stale-market-data",
                ),
                (
                    "overdue_pending_approvals",
                    "warning",
                    0.0,
                    "Pending approvals older than thirty minutes",
                    "docs/OPERATIONS_FAILURE_RUNBOOK.md#overdue-approvals",
                ),
                (
                    "backup_age_hours",
                    "critical",
                    48.0,
                    "Age of the newest available verified backup",
                    "docs/OPERATIONS_FAILURE_RUNBOOK.md#stale-or-missing-backup",
                ),
                (
                    "failed_ai_evaluation",
                    "warning",
                    0.0,
                    "Latest AI regression evaluation failed",
                    "docs/OPERATIONS_FAILURE_RUNBOOK.md#failed-ai-evaluation",
                ),
                (
                    "failed_retrieval_evaluation",
                    "warning",
                    0.0,
                    "Latest governed retrieval evaluation failed",
                    "docs/OPERATIONS_FAILURE_RUNBOOK.md#failed-retrieval-evaluation",
                ),
            ],
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                user_id VARCHAR PRIMARY KEY,
                username VARCHAR NOT NULL UNIQUE,
                password_hash VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                active BOOLEAN NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_token_hash VARCHAR PRIMARY KEY,
                user_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                revoked_at TIMESTAMP,
                last_seen_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                job_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL UNIQUE,
                job_type VARCHAR NOT NULL,
                schedule_seconds BIGINT NOT NULL,
                payload_json VARCHAR NOT NULL,
                enabled BOOLEAN NOT NULL,
                max_retries BIGINT NOT NULL,
                next_run_at TIMESTAMP NOT NULL,
                locked_at TIMESTAMP,
                locked_by VARCHAR,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS job_runs (
                job_run_id VARCHAR PRIMARY KEY,
                job_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                attempt BIGINT NOT NULL,
                worker_id VARCHAR NOT NULL,
                result_json VARCHAR,
                error_type VARCHAR,
                error_message VARCHAR,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'ai_evaluation_v1',
                'Versioned orchestration, authorization, and safety evaluation',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'retrieval_evaluation_v1',
                'Versioned BM25 retrieval ranking and corpus evaluation',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'operational_retention_v1',
                'Explicit preview-only retention and admin-confirmed purge',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'operational_alerts_v1',
                'Persisted alert rules, acknowledgement, and resolution lifecycle',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'backup_verification_evidence_v1',
                'Persist successful temporary restore verification evidence',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'market_news_evidence_v1',
                'Provider-backed market news fetches and deduplicated articles',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'operations_jobs_v1',
                'Persistent scheduled jobs, retries, and operational evidence',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'identity_access_v1',
                'Authenticated users, signed sessions, and role authorization',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO schema_versions VALUES (
                'data_governance_v1',
                'Purpose-aware freshness and governed knowledge retrieval',
                CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            UPDATE data_catalog
            SET data_domain = 'market_data'
            WHERE data_domain IN ('MD', 'md')
            """
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def list_tables(db_path: Path) -> list[str]:
    con = connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_name
            """
        ).fetchall()
    finally:
        con.close()
    return [row[0] for row in rows]


def _rename_legacy_table_if_needed(
    con,
    *,
    table_name: str,
    required_column: str,
    legacy_table_name: str,
) -> None:
    table_exists = con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        """,
        [table_name],
    ).fetchone()[0]
    if not table_exists:
        return

    required_column_exists = con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = 'main'
          AND table_name = ?
          AND column_name = ?
        """,
        [table_name, required_column],
    ).fetchone()[0]
    if required_column_exists:
        return

    legacy_exists = con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        """,
        [legacy_table_name],
    ).fetchone()[0]
    if legacy_exists:
        raise RuntimeError(
            f"Cannot migrate {table_name}: {legacy_table_name} already exists."
        )

    con.execute(f'ALTER TABLE "{table_name}" RENAME TO "{legacy_table_name}"')


def _rename_table_if_exists(
    con,
    *,
    table_name: str,
    legacy_table_name: str,
) -> None:
    table_exists = con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        """,
        [table_name],
    ).fetchone()[0]
    if not table_exists:
        return

    legacy_exists = con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        """,
        [legacy_table_name],
    ).fetchone()[0]
    if legacy_exists:
        raise RuntimeError(
            f"Cannot migrate {table_name}: {legacy_table_name} already exists."
        )

    con.execute(f'ALTER TABLE "{table_name}" RENAME TO "{legacy_table_name}"')


def _add_column_if_missing(
    con,
    *,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    exists = con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = 'main'
          AND table_name = ?
          AND column_name = ?
        """,
        [table_name, column_name],
    ).fetchone()[0]
    if not exists:
        con.execute(f'ALTER TABLE "{table_name}" ADD COLUMN {column_definition}')
