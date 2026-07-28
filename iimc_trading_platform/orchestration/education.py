"""Concepts, refusals, and deflections: what the assistant says when the
answer is knowledge rather than data.

Three related jobs live together because they share one rule — say something
true and useful without touching a tool: explaining a term the user asked
about, declining a question outside the platform's domain, and refusing to
give open-ended investment advice while still pointing at what it *can* do.

Deliberately dependency-free: it imports nothing from the rest of
orchestration, so the router can lean on it without a cycle.
"""

from __future__ import annotations

import re


_EDUCATION_PREFIX_RE = re.compile(
    r"\s*(?:what\s+(?:is|are|does)|explain|define|meaning\s+of"
    r"|tell\s+me\s+about|how\s+does|describe|teach\s+me)\b"
)

_EDUCATION_MAP: dict[str, str] = {
    "rsi": (
        "**RSI (Relative Strength Index)** measures the speed and magnitude "
        "of recent price changes on a 0-100 scale. Traditionally, RSI > 70 "
        "signals overbought conditions (potential sell), RSI < 30 signals "
        "oversold (potential buy). The default period is 14. You can create "
        "a strategy using RSI — try: 'Create a strategy that buys when RSI "
        "is below 30 and exits when RSI is above 70'."
    ),
    "ema": (
        "**EMA (Exponential Moving Average)** gives more weight to recent "
        "prices, making it faster to react than SMA. Traders often compare "
        "a fast EMA (9 or 12) with a slow EMA (21 or 26) — a crossover "
        "above signals bullish momentum, below signals bearish. You can "
        "create EMA crossover strategies in plain language here."
    ),
    "sma": (
        "**SMA (Simple Moving Average)** is the arithmetic mean of prices "
        "over a period. Common periods: 20 (short-term), 50 (medium), 200 "
        "(long-term). The 50/200 crossover is the classic 'golden cross' "
        "(bullish) or 'death cross' (bearish)."
    ),
    "macd": (
        "**MACD (Moving Average Convergence Divergence)** is the difference "
        "between 12-period and 26-period EMA. The signal line is a 9-period "
        "EMA of MACD. Buy when MACD crosses above the signal line, sell "
        "when it crosses below. You can create MACD strategies here."
    ),
    "bollinger": (
        "**Bollinger Bands** are a volatility envelope: a 20-period SMA "
        "(middle band) with upper/lower bands at +/- 2 standard deviations. "
        "Price touching the upper band may signal overbought; the lower "
        "band, oversold. Bandwidth contracting signals a potential breakout."
    ),
    "vwap": (
        "**VWAP (Volume Weighted Average Price)** is the average price "
        "weighted by volume, resetting daily. Institutional traders use it "
        "as a benchmark — buying below VWAP and selling above. It's most "
        "useful for intraday trading."
    ),
    "atr": (
        "**ATR (Average True Range)** measures volatility — the average of "
        "true ranges (max of high-low, |high-prev_close|, |low-prev_close|) "
        "over a period (default 14). It's used for position sizing and "
        "setting stop losses proportional to volatility."
    ),
    "stop loss": (
        "**Stop loss** is a risk management order that closes a position "
        "when price moves against you by a specified amount. Types: fixed "
        "percentage (e.g., 2%), ATR-based (e.g., 1.5x ATR), trailing "
        "(follows price by a fixed distance). All are supported in the "
        "strategy compiler."
    ),
    "pe ratio": (
        "**PE Ratio (Price-to-Earnings)** is the stock price divided by "
        "earnings per share. A high PE (>25) may indicate overvaluation or "
        "growth expectations; a low PE (<15) may suggest undervaluation or "
        "declining earnings. Compare PE within the same sector for context."
    ),
    "market cap": (
        "**Market Capitalization** is the total market value of a company's "
        "outstanding shares (price x shares). Categories: Large-cap (>20K Cr), "
        "Mid-cap (5K-20K Cr), Small-cap (<5K Cr). It indicates company size "
        "and liquidity."
    ),
    "option": (
        "**Options** are contracts giving the right (not obligation) to buy "
        "(call) or sell (put) an asset at a strike price before expiry. Key "
        "concepts: premium, strike price, expiry, intrinsic/extrinsic value, "
        "and Greeks (Delta, Gamma, Theta, Vega). This platform supports "
        "options data import and rule-spec strategies on options OHLCV."
    ),
    "straddle": (
        "**Straddle** is an options strategy: buy both a call and put at "
        "the same strike and expiry. Profits from large price movement in "
        "either direction. Maximum loss is the total premium paid. Used "
        "before events like earnings or budget."
    ),
    "strangle": (
        "**Strangle** is similar to a straddle but uses different strikes — "
        "buy an OTM call and OTM put. Cheaper premium but needs a larger "
        "move to profit. Also used for volatility plays."
    ),
    "iron condor": (
        "**Iron Condor** combines a bull put spread and bear call spread. "
        "Profits when price stays within a range. Limited risk and reward. "
        "Used in low-volatility, range-bound markets."
    ),
    "candlestick": (
        "**Candlestick charts** show open, high, low, close prices per "
        "period. Green/white candles mean close > open (bullish); red/black "
        "mean close < open (bearish). Patterns like doji, hammer, engulfing, "
        "and morning star signal potential reversals."
    ),
    "support resistance": (
        "**Support** is a price level where buying interest prevents further "
        "decline. **Resistance** is where selling pressure prevents further "
        "rise. When support breaks, it often becomes resistance and vice "
        "versa. These levels come from historical price action."
    ),
    "intraday": (
        "**Intraday trading** means opening and closing positions within "
        "the same trading day. Common strategies use 1m, 5m, or 15m "
        "timeframes with indicators like VWAP, EMA crossovers, and RSI. "
        "This platform supports intraday backtesting and paper trading."
    ),
    "swing trading": (
        "**Swing trading** holds positions for days to weeks, capturing "
        "medium-term price moves. Uses daily or 4h timeframes with "
        "indicators like EMA, RSI, and MACD. Less time-intensive than "
        "intraday but requires overnight risk management."
    ),
    "value investing": (
        "**Value investing** (popularized by Warren Buffett and Benjamin "
        "Graham) focuses on buying undervalued stocks based on "
        "fundamentals — low PE, high ROE, strong moat, margin of safety. "
        "This platform has a 'conservative_value' persona for value-oriented "
        "research."
    ),
    "momentum": (
        "**Momentum trading** buys assets that are rising and sells those "
        "falling, based on the tendency for trends to persist. Common "
        "indicators: ROC, RSI, MACD. This platform has built-in momentum "
        "strategies and an 'intraday_momentum' persona."
    ),
    "backtesting": (
        "**Backtesting** applies a trading strategy to historical data to "
        "see how it would have performed. Key metrics: total return, max "
        "drawdown, Sharpe ratio, win rate. This platform runs deterministic "
        "backtests on governed data with full audit trails."
    ),
    "sharpe ratio": (
        "**Sharpe Ratio** measures risk-adjusted returns: (return - risk-free "
        "rate) / standard deviation. A ratio > 1 is good, > 2 is very good, "
        "> 3 is excellent. It penalizes strategies with high volatility."
    ),
    "drawdown": (
        "**Drawdown** is the peak-to-trough decline in portfolio value, "
        "expressed as a percentage. Maximum drawdown measures the worst "
        "historical loss. It's critical for evaluating risk tolerance — "
        "a 50% drawdown needs 100% gain to recover."
    ),
}

def _education_lookup(concept: str) -> str | None:
    concept_lower = concept.lower().strip()
    # Whole-word match, longest key first, so "rsi" never matches inside
    # "dive(rsi)fication" and "moving average" beats "average".
    for key in sorted(_EDUCATION_MAP, key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", concept_lower):
            return _EDUCATION_MAP[key]
    return None

def _educational_response(concept: str) -> str:
    known = _education_lookup(concept)
    if known:
        return known
    return (
        f"I don't have a built-in explanation for '{concept}', but you can "
        f"try: 'search knowledge {concept}' to check your uploaded "
        f"documents, or ask about specific indicators (RSI, EMA, MACD, "
        f"Bollinger, VWAP, ATR), strategies (intraday, swing, momentum, "
        f"value investing), or concepts (stop loss, PE ratio, Sharpe ratio, "
        f"drawdown, options, candlestick patterns)."
    )

_FINANCE_ACRONYMS = {
    "ETF", "ETFS", "IPO", "SIP", "STP", "SWP", "NAV", "GDP", "PE", "EPS",
    "ROE", "ROA", "ROI", "RSI", "EMA", "SMA", "MACD", "ATR", "VWAP", "IV",
    "OI", "REIT", "REITS", "CAGR", "XIRR", "TER", "ELSS", "ULIP", "AMC",
    "NFO", "FD", "RD", "PPF", "NPS", "EBITDA", "PAT", "PBT", "DCF", "PB",
    "US", "UK", "AND", "OR", "VS", "ETC", "FAQ", "CEO", "CFO", "IT",
}

_DOMAIN_TERMS = (
    "trade", "trading", "trader", "stock", "share", "equity", "market",
    "invest", "portfolio", "strategy", "strateg", "backtest", "quote",
    "price", "ltp", "candle", "ohlcv", "dataset", "broker", "openalgo",
    "order", "position", "fund", "margin", "option", "future", "commodity",
    "crypto", "nifty", "sensex", "nse", "bse", "mcx", "rsi", "ema", "sma",
    "macd", "bollinger", "vwap", "atr", "dividend", "earnings", "pe ratio",
    "ipo", "sector", "index", "bull", "bear", "volatility", "hedge",
    "derivative", "sip", "mutual fund", "etf", "forex", "currency",
    "persona", "risk", "approval", "paper", "sandbox", "analyzer",
    "signal", "indicator", "chart", "screener", "news", "sharpe",
    "drawdown", "pnl", "p&l", "profit", "loss", "finance", "financial",
    "filing", "annual report", "balance sheet", "economy", "economic",
    "econom", "recession", "inflation", "interest rate", "gdp", "valuation",
    "fundamental", "buffett", "compound", "wealth", "capital",
)

_OFF_TOPIC_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("weather", ("weather", "temperature outside", "rain today", "forecast for today", "sunny", "humidity")),
    ("creative writing", ("poem", "poetry", "story", "joke", "song", "lyrics", "rap", "haiku", "novel", "essay about")),
    ("homework", ("homework", "assignment", "math problem", "solve this equation", "calculus", "algebra", "physics problem", "chemistry")),
    ("cooking", ("recipe", "cook", "cooking", "bake", "baking", "ingredients for", "dinner ideas")),
    ("sports", ("cricket score", "football score", "match score", "ipl score", "world cup", "who won the match")),
    ("entertainment", ("movie", "movies", "tv show", "netflix", "series to watch", "celebrity", "actor", "actress")),
    ("travel", ("travel to", "vacation", "holiday destination", "flight to", "hotel in", "tourist")),
    ("health", ("medical advice", "doctor", "medicine for", "symptoms", "diagnosis", "headache", "fever")),
    ("general knowledge", ("prime minister", "president of", "capital of", "who is the king", "photosynthesis", "world war")),
    ("programming help", ("write code for", "debug my code", "python script for", "fix my program", "javascript function")),
)

def _off_topic_category(text: str) -> str | None:
    """Detect clearly non-finance requests without touching domain queries."""
    if any(term in text for term in _DOMAIN_TERMS):
        return None
    for category, phrases in _OFF_TOPIC_CATEGORIES:
        if any(phrase in text for phrase in phrases):
            return category
    return None

def _domain_refusal_response(category: str) -> str:
    return (
        f"I'm a trading and market-research assistant, so I can't help with "
        f"{category} questions. I can help with things like:\n"
        "- **Quotes and news**: 'price of Reliance', 'news for Tata Steel'\n"
        "- **Education**: 'what is RSI?', 'explain value investing'\n"
        "- **Strategies**: describe one in plain language and I'll compile "
        "and backtest it\n"
        "- **Your account**: 'show my positions', 'my fund balance'"
    )

_ADVICE_PATTERNS = (
    r"\bwhat\s+should\s+i\s+(?:buy|invest|trade|pick)",
    r"\bwhat\s+to\s+(?:buy|invest)",
    r"\bwhich\s+(?:stock|share)s?\s+should\s+i",
    r"\bwhat\s+(?:stock|share)s?\s+should\s+i",
    r"\bwhich\s+(?:stock|share)s?\s+to\s+(?:buy|invest|trade)",
    # "which stock today is best ...", "which share is the best"
    r"\bwhich\s+(?:stock|share)\b.{0,30}\bbest\b",
    r"\bbest\s+(?:stock|share)s?\s+to\s+(?:buy|invest|trade)",
    r"\bbest\s+(?:stock|share)s?\s+(?:to|for|right\s+now|today|now)\b",
    r"\brecommend\s+(?:a\s+|some\s+|me\s+)?(?:stock|share|trade)",
    r"\bgood\s+(?:stock|share)\s+to\s+(?:buy|invest)",
    r"\bstock\s+tip",
    r"\bmultibagger",
    r"\bbest\s+investment",
)

def _is_open_ended_advice(text: str, message: str) -> bool:
    """Detect vague 'what should I buy' asks with no concrete instrument.

    These are personalised-recommendation requests the platform cannot and
    should not answer blindly. If the user already named a ticker we let the
    normal tools handle it.
    """
    if not any(re.search(pattern, text) for pattern in _ADVICE_PATTERNS):
        return False
    # If they named a concrete uppercase ticker, treat it as a specific ask.
    if re.search(r"\b[A-Z]{3,}\b", message):
        return False
    return True

def _open_ended_advice_response() -> str:
    return (
        "I'm not a licensed financial adviser, so I can't tell you which "
        "stock to buy or pick one for you. But I can help you decide with "
        "real data — tell me what you're after and I'll run it:\n"
        "- **Screen the NIFTY 50**, e.g. 'find NIFTY 50 stocks where RSI is "
        "below 30' or 'NIFTY 50 stocks trading below their 50-day EMA'\n"
        "- **Analyse a specific company**, e.g. 'analyse RELIANCE "
        "fundamentally' or 'price and news for HDFCBANK'\n"
        "- **Backtest an idea**, e.g. 'backtest an EMA crossover on "
        "INFY'\n\n"
        "Which of these would you like, and on which stock or index?"
    )
