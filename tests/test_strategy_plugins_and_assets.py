from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from iimc_trading_platform.db import connect
from iimc_trading_platform.infrastructure import initialize_database
from iimc_trading_platform.services.backtest_service import BacktestService
from iimc_trading_platform.services.market_data_ingestion_service import (
    MarketDataIngestionService,
)


class StrategyPluginsAndAssetsTest(unittest.TestCase):
    def test_local_plugin_runs_on_equity_futures_and_option_ohlcv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "platform.duckdb"
            plugin_directory = root / "strategy_plugins"
            plugin_directory.mkdir()
            _write_plugin(plugin_directory / "range_breakout.py")
            initialize_database(database_path)
            ingestion = MarketDataIngestionService(database_path)
            service = BacktestService(
                database_path,
                strategy_plugin_dir=plugin_directory,
            )
            results = []
            for asset_class, symbol, exchange in (
                ("equity", "RELIANCE", "NSE"),
                ("futures", "NIFTY26JULFUT", "NFO"),
                ("options", "NIFTY26JUL24000CE", "NFO"),
            ):
                dataset_id = f"{asset_class}_plugin_test"
                ingestion.import_ohlcv(
                    dataset_id=dataset_id,
                    asset_class=asset_class,
                    symbol=symbol,
                    exchange=exchange,
                    interval="5m",
                    candles=_candles(),
                    source_name=f"{asset_class}.json",
                )
                results.append(
                    service.run(
                        strategy_name="range_breakout",
                        dataset_id=dataset_id,
                        parameters={"lookback": 3},
                    )
                )

        self.assertEqual(
            [result["status"] for result in results],
            ["completed", "completed", "completed"],
        )
        self.assertTrue(all(result["total_trades"] >= 1 for result in results))
        strategies = service.list_strategies()
        plugin = next(item for item in strategies if item["name"] == "range_breakout")
        self.assertEqual(plugin["origin"], "local_plugin")
        self.assertEqual(
            plugin["supported_asset_classes"],
            ["equity", "futures", "options"],
        )

    def test_chain_options_backtest_selects_a_real_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "platform.duckdb"
            initialize_database(database_path)
            _seed_option_chain(database_path)
            service = BacktestService(database_path)

            instruments = service.list_dataset_instruments("nifty_chain")
            with self.assertRaisesRegex(ValueError, "multiple contracts"):
                service.load_dataset_candles("nifty_chain")
            dataset, candles = service.load_dataset_candles(
                "nifty_chain",
                instrument={
                    "expiry": "26JUL26",
                    "strike": 24000,
                    "option_type": "CE",
                },
            )

        self.assertTrue(instruments["requires_instrument_selection"])
        self.assertEqual(len(instruments["instruments"]), 2)
        self.assertEqual(dataset["asset_class"], "options")
        self.assertEqual(dataset["instrument"]["option_type"], "CALL")
        self.assertEqual(candles[0]["price"], 100.0)


def _write_plugin(path: Path) -> None:
    path.write_text(
        '''from iimc_trading_platform.domain import SignalDirection
from iimc_trading_platform.strategies import RawSignal, StrategyPlugin


class RangeBreakoutStrategy(StrategyPlugin):
    name = "range_breakout"
    version = "1.0.0"
    description = "Example local range-breakout strategy."
    supported_asset_classes = ("equity", "futures", "options")
    parameter_schema = {
        "lookback": {"type": "integer", "default": 3, "minimum": 2, "maximum": 50},
    }

    def generate(self, candles, parameters):
        lookback = parameters["lookback"]
        if len(candles) <= lookback:
            raise ValueError("Need more candles than lookback")
        entry = candles[lookback]
        exit_candle = candles[-1]
        return [
            RawSignal(entry["timestamp"], entry["symbol"], "entry", SignalDirection.LONG,
                      float(entry["price"]), 1.0, "local range breakout", {}),
            RawSignal(exit_candle["timestamp"], exit_candle["symbol"], "exit", SignalDirection.EXIT,
                      float(exit_candle["price"]), 1.0, "local strategy exit", {}),
        ]


def build_strategy():
    return RangeBreakoutStrategy()
''',
        encoding="utf-8",
    )


def _candles() -> list[dict[str, object]]:
    start = datetime(2026, 1, 1, 9, 15)
    return [
        {
            "timestamp": start + timedelta(minutes=5 * index),
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100.5 + index,
            "volume": 1_000 + index,
        }
        for index in range(8)
    ]


def _seed_option_chain(database_path: Path) -> None:
    source_id = "option_chain_source"
    start = datetime(2026, 7, 1, 9, 15)
    rows = []
    for strike, option_type, base in ((24000.0, "CALL", 100.0), (24000.0, "PUT", 80.0)):
        for index in range(6):
            timestamp = start + timedelta(minutes=5 * index)
            rows.append(
                [
                    "NIFTY", "NFO", "26JUL26", "5m", timestamp, "ATM", strike,
                    option_type, base + index, base + index + 2, base + index - 2,
                    base + index, 1_000, 5_000, 15.0, 24_000.0, source_id,
                    "option_chain.csv", "clean", timestamp,
                ]
            )
    con = connect(database_path)
    try:
        con.execute(
            "INSERT INTO raw_file_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [source_id, "local", "option_chain.csv", "hash", 1, start, len(rows), len(rows), 0, 0],
        )
        con.executemany(
            "INSERT INTO options_ohlcv VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        con.execute(
            "INSERT INTO data_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "nifty_chain", "market_data", "options_ohlcv", "NIFTY", "NFO", "5m",
                rows[0][4], rows[-1][4], len(rows), "options_ohlcv", source_id,
                "clean", None, start,
            ],
        )
    finally:
        con.close()


if __name__ == "__main__":
    unittest.main()
