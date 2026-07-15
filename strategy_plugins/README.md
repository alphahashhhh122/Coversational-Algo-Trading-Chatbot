# Local strategy plugins

Put trusted local Python strategy modules in this directory. They are loaded at
platform startup and appear in the Research view beside the built-in strategies.

Each module must expose one of these entrypoints:

- `build_strategy()` returning one `StrategyPlugin`
- `build_strategies()` returning an iterable of `StrategyPlugin` instances
- `STRATEGIES`, an iterable of `StrategyPlugin` instances

Plugins run as local Python code. Only place code you trust in this folder.
Each strategy should declare a stable `name`, `version`, `description`,
`parameter_schema`, and `supported_asset_classes`, then implement `generate`.

The platform passes validated candles with `timestamp`, `symbol`, `open`,
`high`, `low`, `close`, `price`, and `volume`. Use `RawSignal` to return entry
and exit decisions. Generic OHLCV datasets can represent equity, futures,
options, commodities, indexes, or crypto. Chain-style options datasets require
the user to select an expiry, strike, and call/put contract before backtesting.
