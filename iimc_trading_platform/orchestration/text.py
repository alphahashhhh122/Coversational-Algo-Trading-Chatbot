"""Reading a message: symbols, dates, parameters, and what was being asked.

The deterministic router is only as good as what it can pull out of a sentence,
and that extraction is the part most worth testing in isolation — every routing
bug this project has had came from a phrase being read wrong, not from the
dispatch that followed.

Pure functions over strings. Nothing here touches a tool, a registry, or the
network, so it can be exercised directly without building an orchestrator.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from difflib import get_close_matches
from typing import Any

from ..services.instrument_names import company_name as _company_name


_ROUTER_SYSTEM_PROMPT = (
    "You are the orchestration layer for an audited trading platform. Select "
    "only registered tools. Never invent dataset IDs, run IDs, prices, P&L, "
    "risk decisions, order IDs, broker state, news, or market data. Prefer "
    "research/backtest/read-only tools unless the user explicitly asks for "
    "paper or live execution. Live execution must remain guarded by backend "
    "configuration and approval checks. For compound questions, select at "
    "most four read-only tools. Never combine a state-changing tool with "
    "another tool.\n\n"
    "Tool routing guide:\n"
    "- Price/quote questions → get_market_quote\n"
    "- Market news, sector outlook, top gainers/losers → get_market_news\n"
    "- 'What is RSI/MACD/EMA', education → search_knowledge\n"
    "- 'Create/build/make a strategy' → compile_custom_strategy_spec\n"
    "- 'Backtest EMA crossover' → run_backtest\n"
    "- 'My positions/funds/orders' → get_openalgo_snapshot\n"
    "- 'Warren Buffett/conservative' → get_strategy_persona\n"
    "- 'Compare run_X and run_Y' → compare_runs\n"
    "- 'Paper trade/readiness' → get_execution_readiness\n"
    "- 'Import data for SYMBOL' → import_openalgo_history\n"
    "- Symbol comparison ('X vs Y') → two get_market_quote calls\n"
    "- Platform capabilities → get_platform_summary\n"
    "- Dataset listing/info → list_datasets or get_dataset_detail\n"
    "- Approval/risk → list_pending_approvals or get_risk_decisions\n"
    "- Portfolio → get_portfolio_snapshot\n"
    "- OpenAlgo status → get_openalgo_monitor\n\n"
    "You help with trading, markets, investing, finance, economics, "
    "companies, and financial history — including educational, conceptual, "
    "historical, and 'who/which/compare' questions (for example famous "
    "investors, what value investing is, or what caused the 2008 crisis). "
    "When no tool fits such a question, do NOT select a tool and do NOT "
    "refuse — just answer it directly and helpfully. Do not call account, "
    "quote, or broker tools for purely conceptual or comparison questions "
    "('what is X', 'difference between X and Y'). Only decline requests that "
    "are clearly unrelated to finance and markets (weather, sports, cooking, "
    "entertainment, personal or coding help), in one short sentence."
)

def _chat_messages(
    message: str,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    input_items = [
        {"role": item["role"], "content": item["content"]}
        for item in history[-12:]
        if item.get("role") in {"user", "assistant"}
    ]
    input_items.append({"role": "user", "content": message})
    return input_items

def _is_groq_routing_failure(exc: Exception) -> bool:
    """Identify provider failures for which local routing is safe."""
    message = str(exc).lower()
    return "tool_use_failed" in message or "rate_limit_exceeded" in message

def _is_groq_rate_limited(exc: Exception) -> bool:
    return "rate_limit_exceeded" in str(exc).lower()

_INTENT_TERMS = frozenset(
    {
        "account",
        "balance",
        "backtest",
        "cash",
        "current",
        "dataset",
        "funds",
        "headline",
        "latest",
        "margin",
        "market",
        "monitor",
        "news",
        "openalgo",
        "order",
        "orders",
        "outlook",
        "paper",
        "performance",
        "position",
        "positions",
        "price",
        "quote",
        "quotes",
        "research",
        "risk",
        "scenario",
        "share",
        "status",
        "stock",
        "strategy",
        "trade",
        "trades",
        "trading",
    }
)

def _normalize_intent_text(message: str) -> str:
    """Correct only close matches to known intent words, never entities."""
    normalized = message.lower()
    aliases = {
        "ltp": "price",
        "quotation": "quote",
        "rate": "price",
    }

    def normalize(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in aliases:
            return aliases[token]
        if len(token) < 4 or token in _INTENT_TERMS:
            return token
        close = get_close_matches(token, _INTENT_TERMS, n=1, cutoff=0.8)
        return close[0] if close else token

    return re.sub(r"[a-z]+", normalize, normalized)

def _contains_any_word(text: str, words: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE)
        for word in words
    )

def _openalgo_snapshot_types(text: str) -> list[str]:
    categories = (
        ("holdings", ("holding", "holdings")),
        ("positionbook", ("position", "positions")),
        ("orderbook", ("orderbook", "open order", "orders", "order")),
        ("tradebook", ("tradebook", "trade book", "trades", "fills", "my trade")),
        (
            "funds",
            (
                "funds",
                "cash",
                "balance",
                "collateral",
                "margin",
                "available cash",
            ),
        ),
    )
    return [
        snapshot_type
        for snapshot_type, phrases in categories
        if any(phrase in text for phrase in phrases)
    ]

def _is_sandbox_intent_request(text: str) -> bool:
    return (
        any(word in text for word in ("prepare", "create", "draft"))
        and any(
            phrase in text
            for phrase in (
                "sandbox order",
                "paper order",
                "paper trading order",
                "openalgo intent",
                "sandbox intent",
            )
        )
    )

def _parse_technical_screen(text: str) -> dict[str, Any] | None:
    """Parse a technical screen request into run_technical_screen arguments.

    Handles both orderings, e.g. 'stocks where RSI is below 30' and
    'stocks trading below their 50-day EMA'. Defaults to scanning the
    NIFTY 50 so no watchlist is required; 'watchlist' opts into that instead.
    """
    if not re.search(
        r"\b(?:stocks?|symbols?|watch\s*list|screen|scan|nifty)\b", text
    ):
        return None
    if not re.search(r"\b(rsi|ema|sma|volume)\b", text):
        return None

    args: dict[str, Any] = {}
    number_match = re.search(r"(\d+(?:\.\d+)?)", text)
    number = float(number_match.group(1)) if number_match else None
    # The threshold is the number stated after the comparator, so
    # "14-day RSI below 30" reads 30, not the period 14.
    after_comparator = re.search(
        r"\b(?:below|above|over|under|than|higher|lower)\s+(\d+(?:\.\d+)?)",
        text,
    )
    stated = float(after_comparator.group(1)) if after_comparator else None

    if re.search(r"\brsi\b", text):
        above = bool(
            re.search(r"\b(above|over|greater|more\s+than|higher)\b", text)
        )
        args["condition"] = "rsi_above" if above else "rsi_below"
        args["threshold"] = stated if stated is not None else (
            70.0 if above else 30.0
        )
    elif re.search(r"\b(ema|sma|moving\s+average)\b", text):
        below = bool(re.search(r"\b(below|under|less\s+than|lower)\b", text))
        args["condition"] = "price_below_ema" if below else "price_above_ema"
        args["threshold"] = 1.0
        # A period like "50-day" / "50 day" / "50 ema" sizes the average.
        period_match = re.search(
            r"(\d+)\s*(?:-?\s*day|-?\s*period|\s+(?:ema|sma))", text
        )
        if period_match:
            args["period"] = max(2, min(200, int(period_match.group(1))))
    elif re.search(r"\bvolume\b", text):
        args["condition"] = "volume_spike"
        args["threshold"] = stated if stated is not None else (
            number if number is not None else 2.0
        )
    else:
        return None

    args["universe"] = None if re.search(r"\bwatch\s*list\b", text) else "nifty50"
    return args

def _parse_direct_order(message: str, text: str) -> dict[str, Any] | None:
    """Parse a discretionary order like 'buy 10 RELIANCE' or 'sell 5 TCS'.

    Requires an explicit quantity and symbol so nothing is assumed. A risk
    decision id present means the user wants the strategy path, not a
    direct order.
    """
    if _extract_identifier(message, "risk_"):
        return None
    match = re.search(
        r"\b(buy|sell)\s+(\d+)\s+([A-Za-z][\w&-]{1,29})\b",
        message,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    side = match.group(1).upper()
    quantity = int(match.group(2))
    symbol = match.group(3).upper()
    if symbol in {"SHARE", "SHARES", "LOT", "LOTS", "QTY", "UNITS"}:
        return None
    if quantity < 1 or quantity > 100_000:
        return None
    order = {"symbol": symbol, "quantity": quantity, "side": side}
    price_match = re.search(
        r"\b(?:at|@)\s*(?:limit\s+)?(?:rs\.?\s*|₹\s*)?(\d+(?:\.\d+)?)",
        message,
        flags=re.IGNORECASE,
    )
    if price_match and "limit" in text:
        order["order_type"] = "LIMIT"
        order["limit_price"] = float(price_match.group(1))
    if "cnc" in text or "delivery" in text:
        order["product"] = "CNC"
    exchange = _exchange_from_text(message, default="NSE")
    if exchange:
        order["exchange"] = exchange
    return order

def _is_paper_trade_workflow_request(text: str) -> bool:
    if not any(phrase in text for phrase in ("paper trade", "paper trading")):
        return False
    if _is_sandbox_intent_request(text):
        return False
    return not any(
        phrase in text
        for phrase in (
            "show paper",
            "list paper",
            "paper intent",
            "paper order status",
            "openalgo intent",
        )
    )

def _is_history_import_request(text: str) -> bool:
    return (
        any(word in text for word in ("import", "fetch", "download", "ingest", "load"))
        and any(
            phrase in text
            for phrase in (
                "historical data",
                "history data",
                "price history",
                "ohlcv",
                "candles",
                "history for",
            )
        )
        and "order history" not in text
    )

def _references_unspecified_personal_strategy(text: str) -> bool:
    if not re.search(r"\b(?:my|this|the)\s+strategy\b", text):
        return False
    return not any(
        word in text
        for word in (
            "ema", "sma", "rsi", "momentum", "roc", "macd",
            "bollinger", "vwap", "plugin", "custom_", "strategy:",
            "strategy=", "strategy named",
        )
    )

def _closest_action_response(text: str) -> str:
    """Point an unrecognized request at the nearest supported workflow."""
    suggestions: list[str] = []
    if any(word in text for word in ("predict", "forecast", "will", "target price", "tomorrow")):
        suggestions.append(
            "I can't predict prices, but I can ground a view in evidence: "
            "ask for 'news for <symbol>' or 'research <symbol>' for stored "
            "catalysts, or backtest a rule-based idea on real history."
        )
    if any(word in text for word in ("buy", "sell", "invest", "should i")):
        suggestions.append(
            "I don't give personalized investment advice or place live "
            "orders. I can compile a strategy you describe, backtest it, and "
            "prepare a human-approved paper order."
        )
    if "strateg" in text:
        suggestions.append(
            "Describe a strategy in plain language, for example: 'Create a "
            "Reliance 5 minute strategy that buys when EMA 9 crosses above "
            "EMA 21 and exits when EMA 9 crosses below EMA 21 with a 2 "
            "percent stop loss'."
        )
    if any(word in text for word in ("data", "candle", "history", "import")):
        suggestions.append(
            "To bring in market data, say: 'import historical data for "
            "RELIANCE NSE 5m from 2026-06-01 to 2026-07-16'."
        )
    if any(word in text for word in ("option", "call", "put", "strike", "expiry")):
        suggestions.append(
            "For options, I can resolve option symbols ('option symbol for "
            "NIFTY 24000 CE'), import options OHLCV data, and derive "
            "IV/OI features. Describe what you need."
        )
    if any(word in text for word in ("risk", "exposure", "var", "drawdown")):
        suggestions.append(
            "I can show risk decisions for a specific backtest run "
            "('risk for run_abc123'), or check portfolio exposure. "
            "Run a backtest first to generate risk evidence."
        )
    if any(word in text for word in ("commodity", "gold", "silver", "crude", "mcx")):
        suggestions.append(
            "Commodity data flows through OpenAlgo on MCX exchange. Try: "
            "'import historical data for GOLD MCX 5m' or 'price of GOLD MCX'."
        )
    if any(word in text for word in ("crypto", "bitcoin", "btc", "ethereum")):
        suggestions.append(
            "Crypto support depends on your broker's API. If available "
            "through OpenAlgo, you can import and backtest crypto data "
            "the same way as equities."
        )
    if not suggestions:
        suggestions.append(
            "I can help with:\n"
            "- **Quotes**: 'price of Reliance'\n"
            "- **News**: 'news for Tata Steel' or 'market outlook'\n"
            "- **Education**: 'what is RSI?' or 'explain MACD'\n"
            "- **Strategy creation**: describe in plain language\n"
            "- **Backtesting**: 'backtest EMA crossover on RELIANCE'\n"
            "- **Account**: 'my positions' or 'my funds'\n"
            "- **Paper trading**: prepare and approve sandbox orders\n"
            "- **Comparison**: 'Reliance vs TCS'\n"
            "- **Sectors**: 'how is banking sector doing?'"
        )
    return " ".join(suggestions)

def _is_strategy_creation_request(text: str) -> bool:
    """Detect natural-language strategy creation, including rule phrasing."""
    if "strateg" not in text:
        return False
    has_rule_language = bool(
        re.search(
            r"\b(?:buys?|sells?|enters?|exits?|goes?\s+(?:long|short)|"
            r"longs?|shorts?|closes?)\s+(?:[\w&.-]+\s+){0,2}?"
            r"(?:when(?:ever)?|if|once)\b",
            text,
        )
    )
    if has_rule_language:
        return True
    if re.match(r"\s*(?:how|what|which|can|could|do|does|is|are)\b", text):
        return False
    return any(
        word in text
        for word in (
            "create",
            "build",
            "make",
            "draft",
            "design",
            "compile",
            "write",
            "generate",
            "set up",
            "i want",
            "i need",
            "give me",
        )
    )

def _is_market_price_request(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "price",
            "market price",
            "share price",
            "stock price",
            "trading price",
            "current price",
            "latest price",
            "live price",
            "quote",
            "quotes",
            "ltp",
            "what price",
            "what's price",
            "whats price",
        )
    )

def _is_market_outlook_request(text: str) -> bool:
    """Recognize forward-looking research requests before model routing."""
    return any(
        phrase in text
        for phrase in (
            "expected to rise",
            "expected to fall",
            "likely to rise",
            "likely to fall",
            "stocks to buy",
            "stocks to sell",
            "stock picks",
            "next week",
            "next month",
            "weekly outlook",
        )
    )

def _market_outlook_symbol(message: str) -> str | None:
    symbol = _symbol_from_text(message)
    if symbol in {
        "EXPECTED",
        "LIKELY",
        "MONTH",
        "NEXT",
        "RISE",
        "SHOULD",
        "STOCK",
        "STOCKS",
        "WEEK",
        "WHICH",
    }:
        return None
    return symbol

def _market_outlook_query(message: str, symbol: str | None) -> str:
    if symbol:
        return f"{symbol} stock outlook"
    # This query maps to Event Registry's India-market handling and avoids
    # turning a request for a forward prediction into a synthetic stock pick.
    return "NIFTY Indian stock market outlook"

def _is_market_quote_follow_up(
    text: str,
    history: list[dict[str, str]],
) -> bool:
    if not any(
        phrase in text
        for phrase in ("and ", "what about", "how about", "then ")
    ):
        return False
    previous = _last_user_message(history)
    return previous is not None and _is_market_price_request(
        _normalize_intent_text(previous)
    )

def _market_query_for_request(
    message: str,
    history: list[dict[str, str]],
) -> str:
    query = _market_query_from_text(message)
    if query not in {"it", "its", "that", "this"}:
        return query
    previous = _last_user_message(history)
    return _market_query_from_text(previous) if previous else query

def _last_user_message(history: list[dict[str, str]]) -> str | None:
    for item in reversed(history):
        if item.get("role") == "user" and item.get("content"):
            return str(item["content"])
    return None

def _market_query_from_text(message: str) -> str:
    normalized_message = _normalize_intent_text(message)
    without_request_words = re.sub(
        r"\b(what|is|the|market|status|scenario|price|share|stock|current|"
        r"latest|live|quote|quotes|ltp|rate|of|for|please|tell|me|whats|what's|"
        r"and|about|how|then|news|headline|headlines|update|updates|outlook|"
        r"research|today|recent|recently|give|show|get|any|week|next|trend|"
        r"trending|happening|going|on)\b",
        " ",
        normalized_message,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"\s+", " ", without_request_words).strip(" ?,.!:")
    # A bare "latest market news" leaves nothing — fall back to a clean,
    # relevant default rather than echoing the request words back.
    return query[:200] or "Indian stock market"

def _tool_arguments(raw_arguments: str | None) -> dict[str, Any]:
    """Normalize provider tool arguments while preserving strict schemas."""
    parsed = json.loads(raw_arguments or "{}")
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object or null")
    return parsed

def _extract_identifier(text: str, prefix: str) -> str | None:
    matches = re.findall(
        rf"\b{re.escape(prefix)}[A-Za-z0-9_-]+\b",
        text,
    )
    field_placeholder = f"{prefix}id".lower()
    candidates = [
        value
        for value in matches
        if value.lower() != field_placeholder
    ]
    return candidates[-1] if candidates else None

def _extract_identifiers(text: str, prefix: str) -> list[str]:
    return list(
        dict.fromkeys(
            re.findall(
                rf"\b{re.escape(prefix)}[A-Za-z0-9_-]+\b",
                text,
            )
        )
    )

def _dataset_from_text(text: str) -> str | None:
    dataset_id = _extract_identifier(text, "dataset_")
    if dataset_id:
        return _clean_identifier(dataset_id)
    match = re.search(
        r"\bdataset(?:_id| id)\s*[:=]?\s*([A-Za-z0-9_.-]+)",
        text,
        flags=re.IGNORECASE,
    ) or re.search(
        r"\bdataset\s*[:=]\s*([A-Za-z0-9_.-]+)",
        text,
        flags=re.IGNORECASE,
    ) or re.search(
        r"\bon\s+dataset\s+([A-Za-z0-9_.-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        candidate = _clean_identifier(match.group(1))
        if candidate.lower() not in {"id", "dataset"}:
            return candidate
    match = re.search(
        r"\bon\s+(?!dataset\b)([A-Za-z][A-Za-z0-9_.-]+)\b",
        text,
        flags=re.IGNORECASE,
    )
    if match and "dataset" in text.lower():
        return _clean_identifier(match.group(1))
    return None

def _clean_identifier(value: str) -> str:
    return value.strip().strip(".,;:)]}")

def _strategy_from_text(text: str) -> str:
    explicit = re.search(
        r"\b(?:strategy(?:[_\s-]*name)?|plugin)\s*(?:[:=]|named)?\s*"
        r"([a-z][a-z0-9_.-]{1,100})\b",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        candidate = explicit.group(1).lower()
        if candidate not in {"backtest", "run", "test", "strategy"}:
            return candidate
    if "rsi" in text:
        return "rsi_mean_reversion"
    if "sma" in text:
        return "sma_crossover"
    if "momentum" in text or "roc" in text:
        return "momentum_roc"
    return "ema_crossover"

def _persona_from_text(text: str) -> str | None:
    if "buffett" in text or "warren" in text or "value" in text:
        return "conservative_value"
    if "momentum" in text or "intraday" in text:
        return "intraday_momentum"
    if "risk-off" in text or "risk off" in text or "defensive" in text:
        return "risk_off_capital_preservation"
    if "balanced" in text or "systematic" in text:
        return "balanced_systematic"
    return None

def _strategy_parameters(text: str, strategy_name: str) -> dict[str, Any]:
    parameter_block = re.search(
        r"\bparameters?\s*[:=]?\s*(\{.*\})",
        text,
        flags=re.IGNORECASE,
    )
    if parameter_block:
        try:
            parsed = json.loads(parameter_block.group(1))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    numbers = [int(value) for value in re.findall(r"\b\d+\b", text)]
    if strategy_name in {"ema_crossover", "sma_crossover"} and len(numbers) >= 2:
        return {
            "fast_period": numbers[0],
            "slow_period": numbers[1],
        }
    if strategy_name == "rsi_mean_reversion" and numbers:
        return {"period": numbers[0]}
    if strategy_name == "momentum_roc" and numbers:
        return {"period": numbers[0]}
    return {}

def _symbol_from_text(text: str) -> str | None:
    upper = text.upper()
    excluded = {
        # Short English stopwords that otherwise get grabbed as tickers,
        # e.g. "backtest AN EMA crossover ON WIPRO".
        "A", "AN", "AS", "AT", "BE", "BY", "DO", "IF", "IN", "IS", "IT",
        "OF", "ON", "OR", "SO", "TO", "UP", "WE", "AM", "US",
        # Question / auxiliary / common function words. The whole message is
        # upper-cased before matching, so every ordinary word looks like a
        # ticker — these must be filtered or "what is the market outlook" picks
        # up "WHAT" as the symbol.
        "WHAT", "WHATS", "WHEN", "WHENS", "WHERE", "WHY", "HOW", "HOWS",
        "WHO", "WHOM", "WHOSE", "WHICH", "WILL", "SHALL", "SHOULD", "COULD",
        "WOULD", "CAN", "CANT", "CANNOT", "DOES", "DID", "DOING", "ARE",
        "AINT", "WAS", "WERE", "HAS", "HAVE", "HAD", "THIS", "THAT", "THESE",
        "THOSE", "THERE", "THEIR", "THEY", "THEM", "HERE", "THEN", "THAN",
        "WITH", "FROM", "OVER", "UNDER", "WHILE", "ALSO", "JUST", "VERY",
        "MUCH", "MORE", "MOST", "SOME", "ALL", "BOTH", "EACH", "PLEASE",
        "THANKS", "YES", "NOW", "SOON", "YEAR", "MONTH", "DAY", "DAYS",
        "TIME", "GOING", "GONNA", "LIKE", "GOOD", "BAD", "OKAY",
        "ME", "YOU", "HIM", "HER", "HERS", "OUR", "OURS", "MINE", "ITS",
        "I", "HELLO", "HI", "HEY", "PLS",
        # Research/analysis verbs, e.g. "ANALYZE RELIANCE", "RESEARCH TCS".
        "ANALYZE", "ANALYSE", "ANALYSIS", "RESEARCH", "STUDY", "REVIEW",
        "SUMMARIZE", "SUMMARISE", "EXPLAIN", "TELL", "SHOW", "DEEP", "DIVE",
        "FULL", "OVERVIEW", "BRIEFING", "LOOK", "INTO", "STORY",
        # Validation / walk-forward words, e.g. "CHECK the RELIANCE strategy".
        "CHECK", "VALIDATE", "ROBUST", "ROBUSTNESS", "FORWARD", "SAMPLE",
        "OVERFIT", "HOLD", "HOLDS", "WALK", "OPTIMIZE", "OPTIMISE", "TUNE",
        "DISCOVER", "FIND", "BEST", "PROFITABLE", "WINNING",
        # Watch / monitor words, e.g. "WATCH RELIANCE for RSI below 30".
        "WATCH", "WATCHES", "WATCHING", "MONITOR", "ALERT", "NOTIFY",
        "BELOW", "ABOVE", "UNDER", "OVER", "DROPS", "CROSS", "CROSSES",
        "WHEN", "UNWATCH",
        "ABOUT",
        "AND",
        "ANY",
        "API",
        "ATM",
        "BUY",
        "CALL",
        "CE",
        "CNC",
        "DATASET",
        "EMA",
        "FOR",
        "FUTURES",
        "GET",
        "GIVE",
        "HAPPENING",
        "HEADLINE",
        "HEADLINES",
        "IV",
        "LATEST",
        "LIMIT",
        "LIVE",
        "MARKET",
        "MIS",
        "MY",
        "NEWS",
        "NEXT",
        "NFO",
        "NRML",
        "NSE",
        "OHLCV",
        "OPTION",
        "OPTIONS",
        "ORDER",
        "OUTLOOK",
        "PAPER",
        "PE",
        "PREPARE",
        "PUT",
        "RECENT",
        "RESEARCH",
        "RISK",
        "ROC",
        "RSI",
        "SCENARIO",
        "SELL",
        "SHOW",
        "SL",
        "SMA",
        "SPEC",
        "STRATEGY",
        "THE",
        "TODAY",
        "TREND",
        "TRENDING",
        "UPDATE",
        "UPDATES",
        "VIEW",
        "WANT",
        "WEEK",
        "YOUR",
    }
    for pattern in (
        r"\b(?:symbol|ticker|underlying)\s*[:=]?\s*([A-Za-z][A-Za-z0-9&.-]{1,30})\b",
        r"\b(?:for|on|trade|backtest)\s+([A-Za-z][A-Za-z0-9&.-]{1,30})\b",
    ):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            candidate = _clean_symbol(match.group(1))
            if candidate and candidate not in excluded:
                return candidate
    matches = [
        _clean_symbol(value)
        for value in re.findall(r"\b[A-Z][A-Z0-9&.-]{1,30}\b", upper)
    ]
    candidates = [
        value for value in matches
        if value and value not in excluded and not value.startswith("CUSTOM_")
    ]
    return candidates[0] if candidates else None

def _symbols_from_text(message: str, *, limit: int = 3) -> list[str]:
    """Resolve every real instrument named in a message (for comparisons).

    Only tokens that resolve to a known instrument via ``company_name`` count, so
    "compare reliance and tcs" yields [RELIANCE, TCS] while ordinary words are
    ignored. Order-preserving and de-duplicated.
    """

    found: list[str] = []
    for token in re.findall(r"\b[A-Za-z][A-Za-z0-9&.-]{1,19}\b", message):
        candidate = _clean_symbol(token)
        if not candidate or candidate in found:
            continue
        if _company_name(candidate):
            found.append(candidate)
            if len(found) >= limit:
                break
    return found

def _clean_symbol(value: str) -> str:
    return _clean_identifier(value).replace("&", "").replace(".", "").upper()

def _exchange_from_text(text: str, *, default: str = "NSE") -> str:
    upper = text.upper()
    for exchange in ("NFO", "BFO", "NSE", "BSE", "MCX", "CDS", "BCD"):
        if re.search(rf"\b{exchange}\b", upper):
            return exchange
    return default

def _timeframe_from_text(text: str) -> str:
    if re.search(r"\b(day|daily|1d)\b", text):
        return "1d"
    match = re.search(
        r"\b(\d+)\s*(m|min|mins|minute|minutes|h|hr|hour|hours|d|day|days)\b",
        text,
    )
    if not match:
        return "5m"
    value, unit = match.groups()
    normalized = {
        "m": "m",
        "min": "m",
        "mins": "m",
        "minute": "m",
        "minutes": "m",
        "h": "h",
        "hr": "h",
        "hour": "h",
        "hours": "h",
        "d": "d",
        "day": "d",
        "days": "d",
    }[unit]
    return f"{value}{normalized}"

def _readiness_arguments(message: str) -> dict[str, Any]:
    text = message.lower()
    symbol = _symbol_from_text(message) or "MARKET"
    asset_class = _asset_class_from_text(text)
    exchange = _exchange_from_text(
        message,
        default=_default_exchange_for_asset(asset_class),
    )
    start_date, end_date = _date_range_from_text(text)
    return {
        "symbol": symbol,
        "exchange": exchange,
        "asset_class": asset_class,
        "interval": _timeframe_from_text(text),
        "start_date": start_date,
        "end_date": end_date,
    }

def _asset_class_from_text(text: str) -> str:
    if re.search(r"\b(?:crypto|coin|bitcoin|btc|eth)\b", text):
        return "crypto"
    if re.search(r"\b(?:commodity|gold|silver|crude|mcx)\b", text):
        return "commodity"
    if re.search(r"\bfuture(?:s)?\b", text):
        return "futures"
    if re.search(r"\b(?:option|options|ce|pe|call|put)\b", text):
        return "options"
    if re.search(r"\bindex\b", text):
        return "index"
    return "equity"

def _default_exchange_for_asset(asset_class: str) -> str:
    return {
        "commodity": "MCX",
        "crypto": "CRYPTO",
        "futures": "NFO",
        "options": "NFO",
        "index": "NSE",
    }.get(asset_class, "NSE")

def _date_range_from_text(text: str) -> tuple[str, str]:
    dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], dates[0]
    match = re.search(r"\blast\s+(\d{1,4})\s+(day|days)\b", text)
    if match:
        days = max(1, min(int(match.group(1)), 3650))
        end = date.today()
        start = end - timedelta(days=days)
        return start.isoformat(), end.isoformat()
    end = date.today()
    start = end - timedelta(days=30)
    return start.isoformat(), end.isoformat()

def _sandbox_intent_arguments(message: str, decision_id: str) -> dict[str, Any]:
    text = message.lower()
    upper = message.upper()
    quantity_match = re.search(r"\b(?:qty|quantity)\s*[:=]?\s*(\d+)\b", text)
    if quantity_match is None:
        quantity_match = re.search(r"\b(\d+)\s+(?:share|shares|lot|lots|qty)\b", text)
    if quantity_match is None:
        quantity_match = re.search(r"\b(?:buy|sell|short)\s+(\d+)\b", text)
    order_type = "MARKET"
    for candidate in ("SL-M", "LIMIT", "MARKET", "SL"):
        if candidate.lower() in text:
            order_type = candidate
            break
    exchange = "NSE"
    for candidate in ("NFO", "NSE", "BSE", "BFO", "MCX", "CDS", "BCD"):
        if re.search(rf"\b{candidate}\b", upper):
            exchange = candidate
            break
    product = "MIS"
    for candidate in ("NRML", "CNC", "MIS"):
        if re.search(rf"\b{candidate}\b", upper):
            product = candidate
            break
    strategy_match = re.search(
        r"\b(?:strategy|strategy_name)\s*[:=]\s*([A-Za-z0-9_.-]+)",
        message,
        flags=re.IGNORECASE,
    )
    limit_match = re.search(r"\blimit(?: price)?\s*[:=]?\s*(\d+(?:\.\d+)?)\b", text)
    trigger_match = re.search(r"\btrigger(?: price)?\s*[:=]?\s*(\d+(?:\.\d+)?)\b", text)
    return {
        "decision_id": decision_id,
        "symbol": _symbol_from_text(message) or "NIFTY",
        "exchange": exchange,
        "side": "SELL" if "sell" in text or "short" in text else "BUY",
        "product": product,
        "order_type": order_type,
        "quantity": int(quantity_match.group(1)) if quantity_match else 1,
        "strategy_name": (
            strategy_match.group(1)
            if strategy_match
            else _strategy_from_text(message)
        ),
        "limit_price": float(limit_match.group(1)) if limit_match else None,
        "trigger_price": float(trigger_match.group(1)) if trigger_match else None,
        "requested_by": "chat_user",
    }
