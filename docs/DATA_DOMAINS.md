# Platform Data Domains

The data catalog uses `DataDomain` values to classify datasets consistently.

## External And Research Data

- `market_data`: prices, OHLCV, options chains, volume, OI, IV, quotes
- `fundamental_data`: financial statements, earnings, ratios, company metrics
- `alternative_data`: news, sentiment, macro, event, or other external datasets
- `reference_data`: symbols, exchanges, expiries, lot sizes, calendars

## Generated Trading Data

- `strategy_data`: strategy definitions, parameters, versions, and runs
- `signal_data`: entry, exit, rebalance, and alert signals
- `risk_data`: policies, checks, approvals, rejections, and exposure
- `order_data`: order intents, requests, and lifecycle events
- `trade_data`: fills and executions
- `position_data`: open positions, holdings, and exposure
- `funds_data`: balances, margin, available funds, and blocked funds
- `performance_data`: P&L, drawdown, returns, fees, and comparisons

## Application And Governance Data

- `conversation_data`: sessions, messages, and user requests
- `audit_data`: tool calls and immutable event timelines
- `report_data`: generated reports and visual artifacts
- `system_data`: configuration metadata, health, and operational state

The domain identifies what a dataset represents. The `data_type` field provides
the more specific format, such as `options_ohlcv`, `news_sentiment`, or
`order_events`.
