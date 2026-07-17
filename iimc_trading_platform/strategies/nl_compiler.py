"""Deterministic natural-language strategy compiler.

Parses common trading-strategy language (indicator crossovers, thresholds,
stop loss / take profit / trailing stop, timeframe, symbol, session windows,
position sizing, long/short) into the governed rule-spec vocabulary executed
by the native rule-spec runtime. The compiler never generates code and never
invents data: clauses it cannot parse are surfaced verbatim for human review
instead of being silently dropped or replaced with defaults.

External/alternative feature references (news sentiment, open interest,
IV skew, earnings, fundamentals) are only mapped to governed feature inputs
when the user explicitly names them.
"""

from __future__ import annotations

import re
from typing import Any


# Words that must never be treated as a tradable symbol.
_SYMBOL_STOPWORDS = {
    "A", "ABOVE", "AN", "AND", "ATR", "AVERAGE", "BAND", "BANDS", "BAR",
    "BARS", "BELOW", "BOLLINGER", "BUILD", "BUY", "BUYS", "CALL", "CANDLE",
    "CANDLES", "CLOSE", "COMPILE", "CREATE", "CROSS", "CROSSES", "CROSSOVER",
    "CUSTOM", "DAILY", "DAY", "DESIGN", "DRAFT", "EMA", "ENTER", "ENTERS",
    "EQUITY", "EXIT", "EXITS", "FOR", "FROM", "FUTURE", "FUTURES",
    "GENERATE", "GIVE", "HIGH", "HOUR", "HOURLY", "HOURS", "INDEX",
    "INTRADAY", "LINE", "LONG", "LOSS", "LOW", "LOWER", "MACD", "MAKE",
    "MARKET", "MIDDLE", "MINUTE", "MINUTES", "MOMENTUM", "MOVING", "NEED",
    "NEW", "ONLY", "OPEN", "OPTION", "OPTIONS", "PERCENT", "PERIOD",
    "PLEASE", "POSITION", "PRICE", "PROFIT", "PUT", "QUANTITY", "ROC",
    "RSI", "SELL", "SELLS", "SHARE", "SHARES", "SHORT", "SIGNAL", "SIMPLE",
    "SMA", "STOP", "STRATEGY", "TAKE", "THAT", "THE", "TRADE", "TRADES",
    "TRADING", "TRAILING", "UPPER", "USING", "VOLUME", "VWAP", "WANT",
    "WEEKLY", "WHEN", "WHENEVER", "WITH", "WRITE",
}

_EXTERNAL_FEATURES = (
    ("news_sentiment", ("news sentiment", "sentiment score", "sentiment")),
    ("open_interest", ("open interest", "open_interest")),
    ("iv_skew", ("iv skew", "implied volatility", "iv_skew")),
    ("earnings_surprise", ("earnings surprise", "earnings")),
    ("fundamental_score", ("fundamental", "valuation score")),
)

_CROSS_ABOVE_WORDS = (
    "crosses above", "cross above", "crosses over", "cross over",
    "crossing above", "breaks above", "break above", "moves above",
    "rises above", "climbs above",
)
_CROSS_BELOW_WORDS = (
    "crosses below", "cross below", "crosses under", "cross under",
    "crossing below", "breaks below", "break below", "moves below",
    "falls below", "drops below", "dips below",
)
_GT_WORDS = (
    "is greater than or equal to", "greater than or equal to", "at or above",
    "is above", "is over", "is greater than", "greater than", "is higher than",
    "higher than", "is more than", "more than", "exceeds", "above", "over",
)
_LT_WORDS = (
    "is less than or equal to", "less than or equal to", "at or below",
    "is below", "is under", "is less than", "less than", "is lower than",
    "lower than", "below", "under",
)

_ENTRY_TRIGGER = re.compile(
    r"(?:buys?|enters?(?:\s+(?:a\s+)?(?:long|short|position|trade))?|"
    r"goes?\s+(?:long|short)|longs?|shorts?|opens?\s+(?:a\s+)?position)"
    r"(?:\s+[a-z&.-]+){0,2}?\s+(?:when(?:ever)?|if|once)\b",
    flags=re.IGNORECASE,
)
_EXIT_TRIGGER = re.compile(
    r"(?:exits?(?:\s+(?:the\s+)?(?:position|trade))?|sells?|closes?"
    r"(?:\s+(?:the\s+)?(?:position|trade))?|squares?\s*(?:off|out)|"
    r"covers?|books?\s+(?:profit|out))"
    r"\s+(?:when(?:ever)?|if|once)\b",
    flags=re.IGNORECASE,
)


def compile_strategy_text(
    text: str,
    *,
    default_symbol: str | None = None,
    default_timeframe: str = "5m",
) -> dict[str, Any]:
    """Compile plain-language strategy text into a governed rule spec."""
    working = " ".join(str(text or "").split())
    lower = working.lower()
    warnings: list[str] = []
    provenance: list[dict[str, str]] = []
    unparsed: list[str] = []

    risk, lower = _extract_risk(lower, provenance, warnings)
    session, lower = _extract_session(lower, provenance)
    timeframe = _extract_timeframe(lower) or default_timeframe
    if _extract_timeframe(lower) is None:
        warnings.append(
            f"No timeframe was stated; defaulted to {default_timeframe}."
        )
    position_side = _extract_position_side(lower)
    symbol = _extract_symbol(working) or (default_symbol or "").upper() or None
    if not symbol:
        symbol = "MARKET"
        warnings.append(
            "No instrument symbol was recognized; set the symbol before "
            "saving this strategy."
        )

    state = _CompileState()
    # Segment the risk/session-stripped text so already-consumed clauses
    # (stop loss, sizing, session windows) cannot corrupt rule parsing.
    entry_segments, exit_segments = _split_segments(lower)
    entry_rules = _parse_segment_rules(
        entry_segments, state, provenance, unparsed, "entry"
    )
    exit_rules = _parse_segment_rules(
        exit_segments, state, provenance, unparsed, "exit"
    )
    if not entry_rules and not exit_rules:
        entry_rules, exit_rules = _default_template_rules(
            lower, state, warnings
        )
    if not entry_rules:
        warnings.append(
            "No entry condition was recognized "
            "(e.g. 'buy when EMA 9 crosses above EMA 21')."
        )
    if not exit_rules:
        warnings.append(
            "No exit condition was recognized; add an exit rule or a stop "
            "loss / take profit before saving."
        )

    feature_inputs = _explicit_feature_inputs(
        lower, state.external_refs, warnings
    )

    spec: dict[str, Any] = {
        "name": _strategy_name(symbol, state, working),
        "description": working[:1000],
        "symbol": symbol,
        "timeframe": timeframe,
        "indicators": state.indicators,
        "entry_rules": entry_rules,
        "exit_rules": exit_rules,
        "feature_inputs": feature_inputs,
        "risk": risk,
        "position_side": position_side,
    }
    if session:
        spec["session"] = session

    if not exit_rules and (
        risk.get("stop_loss_pct") or risk.get("take_profit_pct")
        or risk.get("trailing_stop_pct")
    ):
        # Risk exits are handled natively; add an explicit always-false
        # rule placeholder is NOT allowed, so surface guidance instead.
        warnings.append(
            "Only risk-based exits (stop/target/trailing) were stated; "
            "an explicit exit rule is still required by the rule runtime."
        )

    return {
        "source_text": working,
        "spec": spec,
        "provenance": provenance,
        "warnings": warnings,
        "unparsed_clauses": unparsed,
    }


class _CompileState:
    def __init__(self) -> None:
        self.indicators: list[dict[str, Any]] = []
        self.indicator_refs: set[str] = set()
        self.external_refs: set[str] = set()
        self.labels: list[str] = []

    def add_indicator(self, ref: str, definition: dict[str, Any]) -> str:
        if ref not in self.indicator_refs:
            self.indicator_refs.add(ref)
            self.indicators.append(definition)
        return ref


def _split_segments(text: str) -> tuple[list[str], list[str]]:
    """Split text into entry and exit condition segments."""
    markers: list[tuple[int, int, str]] = []
    for match in _ENTRY_TRIGGER.finditer(text):
        kind = "entry"
        matched = match.group(0).lower()
        # "sells when" style verbs are exits unless the strategy is short.
        markers.append((match.start(), match.end(), kind))
    for match in _EXIT_TRIGGER.finditer(text):
        markers.append((match.start(), match.end(), "exit"))
    markers.sort(key=lambda item: item[0])

    deduped: list[tuple[int, int, str]] = []
    for marker in markers:
        if deduped and marker[0] < deduped[-1][1]:
            continue
        deduped.append(marker)

    entries: list[str] = []
    exits: list[str] = []
    for index, (start, end, kind) in enumerate(deduped):
        segment_end = (
            deduped[index + 1][0] if index + 1 < len(deduped) else len(text)
        )
        segment = _strip_dangling(text[end:segment_end])
        if not segment:
            continue
        if kind == "entry":
            entries.append(segment)
        else:
            exits.append(segment)
    return entries, exits


def _default_template_rules(
    lower: str,
    state: _CompileState,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build standard rules when only indicators/features were named.

    Every applied template is disclosed in warnings so the user can review
    and edit the generated rules before saving anything.
    """
    entries: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []

    def disclose(label: str) -> None:
        warnings.append(
            f"No explicit entry/exit conditions were stated; applied a "
            f"standard {label} template. Review and edit it before saving."
        )

    for feature_name, phrases in _EXTERNAL_FEATURES:
        if any(
            re.search(rf"\b{re.escape(phrase)}\b", lower)
            for phrase in phrases
        ):
            state.external_refs.add(feature_name)
            entries.append(
                {"left": feature_name, "operator": ">", "right": 0, "joiner": "AND"}
            )
            exits.append(
                {"left": feature_name, "operator": "<=", "right": 0, "joiner": "OR"}
            )
            disclose(f"{feature_name} threshold")
            break

    if re.search(r"\bema\b", lower):
        left = state.add_indicator(
            "EMA_9", {"type": "EMA", "period": 9, "source": "close"}
        )
        right = state.add_indicator(
            "EMA_21", {"type": "EMA", "period": 21, "source": "close"}
        )
        state.labels.append("ema")
        entries.append(
            {"left": left, "operator": "crosses_above", "right": right, "joiner": "AND"}
        )
        exits.append(
            {"left": left, "operator": "crosses_below", "right": right, "joiner": "OR"}
        )
        disclose("EMA 9/21 crossover")
    if re.search(r"\bsma\b|\bsimple moving average\b", lower):
        left = state.add_indicator(
            "SMA_20", {"type": "SMA", "period": 20, "source": "close"}
        )
        right = state.add_indicator(
            "SMA_50", {"type": "SMA", "period": 50, "source": "close"}
        )
        state.labels.append("sma")
        entries.append(
            {"left": left, "operator": "crosses_above", "right": right, "joiner": "AND"}
        )
        exits.append(
            {"left": left, "operator": "crosses_below", "right": right, "joiner": "OR"}
        )
        disclose("SMA 20/50 crossover")
    if re.search(r"\bmacd\b", lower):
        base = {
            "source": "close",
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
        }
        state.add_indicator(
            "MACD_LINE", {"name": "MACD_LINE", "type": "MACD", **base}
        )
        state.add_indicator(
            "MACD_SIGNAL", {"name": "MACD_SIGNAL", "type": "MACD_SIGNAL", **base}
        )
        state.labels.append("macd")
        entries.append(
            {"left": "MACD_LINE", "operator": "crosses_above", "right": "MACD_SIGNAL", "joiner": "AND"}
        )
        exits.append(
            {"left": "MACD_LINE", "operator": "crosses_below", "right": "MACD_SIGNAL", "joiner": "OR"}
        )
        disclose("MACD signal crossover")
    if re.search(r"\bbollinger\b|\bbands?\b", lower):
        state.add_indicator(
            "BB_MIDDLE",
            {
                "name": "BB_MIDDLE",
                "type": "BB_MIDDLE",
                "period": 20,
                "source": "close",
                "stddev": 2.0,
            },
        )
        state.labels.append("bollinger")
        entries.append(
            {"left": "price", "operator": "crosses_above", "right": "BB_MIDDLE", "joiner": "AND"}
        )
        exits.append(
            {"left": "price", "operator": "crosses_below", "right": "BB_MIDDLE", "joiner": "OR"}
        )
        disclose("Bollinger mid-band crossover")
    if re.search(r"\bvwap\b", lower):
        state.add_indicator(
            "VWAP", {"name": "VWAP", "type": "VWAP", "source": "close"}
        )
        state.labels.append("vwap")
        entries.append(
            {"left": "price", "operator": "crosses_above", "right": "VWAP", "joiner": "AND"}
        )
        exits.append(
            {"left": "price", "operator": "crosses_below", "right": "VWAP", "joiner": "OR"}
        )
        disclose("VWAP crossover")
    if re.search(r"\brsi\b", lower):
        ref = state.add_indicator(
            "RSI_14", {"type": "RSI", "period": 14, "source": "close"}
        )
        state.labels.append("rsi")
        entries.append(
            {"left": ref, "operator": "<", "right": 60, "joiner": "AND"}
        )
        exits.append(
            {"left": ref, "operator": ">", "right": 75, "joiner": "OR"}
        )
        disclose("RSI 14 filter")
    if re.search(r"\broc\b|\bmomentum\b|\brate of change\b", lower):
        ref = state.add_indicator(
            "ROC_10", {"type": "ROC", "period": 10, "source": "close"}
        )
        state.labels.append("roc")
        entries.append(
            {"left": ref, "operator": ">", "right": 0, "joiner": "AND"}
        )
        exits.append(
            {"left": ref, "operator": "<=", "right": 0, "joiner": "OR"}
        )
        disclose("momentum (ROC 10) threshold")

    if entries:
        entries[0]["joiner"] = "AND"
    if exits:
        exits[0]["joiner"] = "AND"
    return entries, exits


def _strip_dangling(segment: str) -> str:
    """Remove connector fragments left behind by removed risk clauses."""
    previous = None
    current = segment.strip(" ,.;")
    while previous != current:
        previous = current
        current = re.sub(
            r"[\s,;]*\b(?:with|and|or|then|a|an)\s*$",
            "",
            current,
            flags=re.IGNORECASE,
        ).strip(" ,.;")
    return current


def _parse_segment_rules(
    segments: list[str],
    state: _CompileState,
    provenance: list[dict[str, str]],
    unparsed: list[str],
    rule_kind: str,
) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for segment in segments:
        for clause, joiner in _split_conditions(segment):
            rule = _parse_condition(clause, state)
            if rule is None:
                unparsed.append(clause)
                continue
            rule["joiner"] = joiner
            rules.append(rule)
            provenance.append(
                {
                    "clause": clause,
                    "interpreted_as": (
                        f"{rule_kind}: {rule['left']} {rule['operator']} "
                        f"{rule['right']}"
                    ),
                }
            )
    if rules:
        rules[0]["joiner"] = "AND"
    return rules


def _split_conditions(segment: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    tokens = re.split(r"\s+(and|or)\s+", segment, flags=re.IGNORECASE)
    current_joiner = "AND"
    for token in tokens:
        stripped = _strip_dangling(token)
        if not stripped:
            continue
        if stripped.lower() == "and":
            current_joiner = "AND"
            continue
        if stripped.lower() == "or":
            current_joiner = "OR"
            continue
        parts.append((stripped, current_joiner))
    return parts


def _parse_condition(
    clause: str,
    state: _CompileState,
) -> dict[str, Any] | None:
    lower = clause.lower().strip(" ,.;")
    for phrases, operator in (
        (_CROSS_ABOVE_WORDS, "crosses_above"),
        (_CROSS_BELOW_WORDS, "crosses_below"),
        (_GT_WORDS, ">"),
        (_LT_WORDS, "<"),
        (("is equal to", "equals", "is exactly", "=="), "=="),
        ((">=",), ">="),
        (("<=",), "<="),
        ((">",), ">"),
        (("<",), "<"),
    ):
        for phrase in phrases:
            if phrase in (" ", ""):
                continue
            pattern = (
                re.escape(phrase)
                if not phrase[0].isalpha()
                else rf"\b{re.escape(phrase)}\b"
            )
            match = re.search(pattern, lower)
            if not match:
                continue
            left_text = lower[: match.start()].strip()
            right_text = lower[match.end():].strip()
            left = _parse_operand(left_text, state)
            right = _parse_operand(right_text, state)
            if left is None or right is None:
                return None
            return {"left": left, "operator": operator, "right": right}
    return None


def _parse_operand(text: str, state: _CompileState) -> str | float | None:
    cleaned = text.strip(" ,.;")
    cleaned = re.sub(
        r"^(?:the|its|his|her|their|a|an)\s+", "", cleaned
    ).strip()
    if not cleaned:
        return None

    number = re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned)
    if number:
        return float(cleaned)
    percent = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(?:%|percent)", cleaned)
    if percent:
        value = float(percent.group(1))
        if value > 1:
            return value
        return value / 100.0

    for feature_name, phrases in _EXTERNAL_FEATURES:
        for phrase in phrases:
            if re.search(rf"\b{re.escape(phrase)}\b", cleaned):
                state.external_refs.add(feature_name)
                return feature_name

    parsed = _parse_indicator_operand(cleaned, state)
    if parsed is not None:
        return parsed

    field = re.fullmatch(
        r"(?:the\s+)?(price|close|open|high|low|volume)(?:\s+price)?",
        cleaned,
    )
    if field:
        return field.group(1)
    if re.fullmatch(r"(?:closing|opening)\s+price", cleaned):
        return "close" if cleaned.startswith("clos") else "open"
    if cleaned in {"it", "the stock", "the price", "price"}:
        return "price"
    return None


def _parse_indicator_operand(
    cleaned: str,
    state: _CompileState,
) -> str | None:
    ema_sma = re.fullmatch(
        r"(?:(\d+)[\s-]*(?:period|day|bar|minute|min|hour)?[\s-]*)?"
        r"(ema|sma|exponential moving average|simple moving average|"
        r"moving average)"
        r"[\s(]*(\d+)?[\s)]*",
        cleaned,
    )
    if ema_sma:
        period = ema_sma.group(1) or ema_sma.group(3)
        kind_text = ema_sma.group(2)
        kind = (
            "EMA"
            if kind_text.startswith(("ema", "exponential"))
            else "SMA"
        )
        if period is None:
            return None
        ref = f"{kind}_{int(period)}"
        state.add_indicator(
            ref,
            {"type": kind, "period": int(period), "source": "close"},
        )
        state.labels.append(kind.lower())
        return ref

    rsi = re.fullmatch(r"rsi\s*\(?\s*(\d+)?\s*\)?", cleaned)
    if rsi:
        period = int(rsi.group(1) or 14)
        ref = f"RSI_{period}"
        state.add_indicator(
            ref,
            {"type": "RSI", "period": period, "source": "close"},
        )
        state.labels.append("rsi")
        return ref

    macd = re.fullmatch(
        r"macd(?:\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\))?"
        r"(?:\s+(line|signal|signal line))?",
        cleaned,
    )
    if macd:
        fast = int(macd.group(1) or 12)
        slow = int(macd.group(2) or 26)
        signal = int(macd.group(3) or 9)
        is_signal = bool(macd.group(4) and "signal" in macd.group(4))
        base = {
            "source": "close",
            "fast_period": fast,
            "slow_period": slow,
            "signal_period": signal,
        }
        state.add_indicator(
            "MACD_LINE",
            {"name": "MACD_LINE", "type": "MACD", **base},
        )
        state.add_indicator(
            "MACD_SIGNAL",
            {"name": "MACD_SIGNAL", "type": "MACD_SIGNAL", **base},
        )
        state.labels.append("macd")
        return "MACD_SIGNAL" if is_signal else "MACD_LINE"
    if re.fullmatch(r"(?:the\s+)?(?:macd\s+)?signal(?:\s+line)?", cleaned):
        if "MACD_SIGNAL" in state.indicator_refs:
            state.labels.append("macd")
            return "MACD_SIGNAL"

    bollinger = re.fullmatch(
        r"(?:the\s+)?(upper|lower|middle|mid)\s+(?:bollinger\s+)?band"
        r"(?:\s*\(\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\))?",
        cleaned,
    ) or re.fullmatch(
        r"(?:the\s+)?bollinger\s+(upper|lower|middle|mid)(?:\s+band)?",
        cleaned,
    )
    if bollinger:
        band = bollinger.group(1)
        period = int((bollinger.group(2) if bollinger.lastindex and bollinger.lastindex >= 2 else None) or 20)
        stddev = float((bollinger.group(3) if bollinger.lastindex and bollinger.lastindex >= 3 else None) or 2.0)
        band_type = {
            "upper": "BB_UPPER",
            "lower": "BB_LOWER",
            "middle": "BB_MIDDLE",
            "mid": "BB_MIDDLE",
        }[band]
        state.add_indicator(
            band_type,
            {
                "name": band_type,
                "type": band_type,
                "period": period,
                "source": "close",
                "stddev": stddev,
            },
        )
        state.labels.append("bollinger")
        return band_type

    atr = re.fullmatch(r"atr\s*\(?\s*(\d+)?\s*\)?", cleaned)
    if atr:
        period = int(atr.group(1) or 14)
        ref = f"ATR_{period}"
        state.add_indicator(
            ref,
            {"type": "ATR", "period": period, "source": "close"},
        )
        state.labels.append("atr")
        return ref

    if re.fullmatch(r"(?:the\s+)?vwap", cleaned):
        state.add_indicator(
            "VWAP",
            {"name": "VWAP", "type": "VWAP", "source": "close"},
        )
        state.labels.append("vwap")
        return "VWAP"

    roc = re.fullmatch(
        r"(?:roc|rate of change|momentum)\s*\(?\s*(\d+)?\s*\)?",
        cleaned,
    )
    if roc:
        period = int(roc.group(1) or 10)
        ref = f"ROC_{period}"
        state.add_indicator(
            ref,
            {"type": "ROC", "period": period, "source": "close"},
        )
        state.labels.append("roc")
        return ref
    return None


def _extract_risk(
    lower: str,
    provenance: list[dict[str, str]],
    warnings: list[str],
) -> tuple[dict[str, Any], str]:
    risk: dict[str, Any] = {}

    def note(field: str, value: Any, clause: str) -> None:
        risk[field] = value
        provenance.append(
            {"clause": clause.strip(), "interpreted_as": f"risk.{field} = {value}"}
        )

    trailing = re.search(
        r"([\d.]+)\s*(?:%|percent|per cent|pct)\s*trailing\s*stop(?:[\s-]*loss)?",
        lower,
    ) or re.search(
        r"trailing\s*stop(?:[\s-]*loss)?\s*(?:of|at|is|=|:)?\s*"
        r"([\d.]+)\s*(?:%|percent|per cent|pct)",
        lower,
    )
    if trailing:
        note(
            "trailing_stop_pct",
            round(float(trailing.group(1)) / 100.0, 6),
            trailing.group(0),
        )
        lower = lower.replace(trailing.group(0), " ")

    stop = re.search(
        r"([\d.]+)\s*(?:%|percent|per cent|pct)\s*stop(?:[\s-]*loss)?",
        lower,
    ) or re.search(
        r"stop(?:[\s-]*loss)?\s*(?:of|at|is|=|:)?\s*"
        r"([\d.]+)\s*(?:%|percent|per cent|pct)",
        lower,
    ) or re.search(r"([\d.]+)\s*(?:%|percent|per cent|pct)\s*sl\b", lower)
    if stop:
        note(
            "stop_loss_pct",
            round(float(stop.group(1)) / 100.0, 6),
            stop.group(0),
        )
        lower = lower.replace(stop.group(0), " ")

    target = re.search(
        r"([\d.]+)\s*(?:%|percent|per cent|pct)\s*"
        r"(?:take[\s-]*profit|profit[\s-]*target|target)",
        lower,
    ) or re.search(
        r"(?:take[\s-]*profit|profit[\s-]*target|target)\s*"
        r"(?:of|at|is|=|:)?\s*([\d.]+)\s*(?:%|percent|per cent|pct)",
        lower,
    )
    if target:
        note(
            "take_profit_pct",
            round(float(target.group(1)) / 100.0, 6),
            target.group(0),
        )
        lower = lower.replace(target.group(0), " ")

    size = re.search(
        r"(?:position\s+size|quantity|qty)\s*(?:of|=|:)?\s*(\d+)\b",
        lower,
    ) or re.search(r"\b(\d+)\s+(?:shares|lots|units)\b", lower)
    if size:
        note("max_position_size", int(size.group(1)), size.group(0))
        lower = lower.replace(size.group(0), " ")

    if "stop loss" in lower and "stop_loss_pct" not in risk:
        warnings.append(
            "A stop loss was mentioned but its percentage was not "
            "recognized; set risk.stop_loss_pct explicitly."
        )
    return risk, lower


def _extract_session(
    lower: str,
    provenance: list[dict[str, str]],
) -> tuple[dict[str, str] | None, str]:
    match = re.search(
        r"(?:only\s+)?(?:trades?\s+)?(?:between|from)\s+"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s+"
        r"(?:and|to|until|till)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        lower,
    )
    if not match:
        return None, lower
    start = _to_clock(match.group(1), match.group(2), match.group(3))
    end = _to_clock(match.group(4), match.group(5), match.group(6))
    if start is None or end is None or start >= end:
        return None, lower
    session = {"start": start, "end": end}
    provenance.append(
        {
            "clause": match.group(0),
            "interpreted_as": f"session window {start}-{end}",
        }
    )
    return session, lower.replace(match.group(0), " ")


def _to_clock(
    hour_text: str,
    minute_text: str | None,
    meridiem: str | None,
) -> str | None:
    hour = int(hour_text)
    minute = int(minute_text or 0)
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _extract_timeframe(lower: str) -> str | None:
    if re.search(r"\b(daily|1d|eod|end of day)\b", lower):
        return "1d"
    if re.search(r"\b(weekly|1w)\b", lower):
        return "1w"
    if re.search(r"\bhourly\b", lower):
        return "1h"
    match = re.search(
        r"\b(\d+)\s*[- ]?(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|"
        r"d|day|days|w|wk|week|weeks)\b",
        lower,
    )
    if not match:
        return None
    first_char = match.group(2)[0]
    unit = {"m": "m", "h": "h", "d": "d", "w": "w"}[first_char]
    return f"{match.group(1)}{unit}"


def _extract_position_side(lower: str) -> str:
    if re.search(r"\b(?:go(?:es)?\s+short|shorts?\s+when|short\s+strategy|sell\s+short)\b", lower):
        return "short"
    return "long"


def _extract_symbol(text: str) -> str | None:
    strategy_subject = re.search(
        r"\b(?:a|an|the)\s+((?:[A-Za-z0-9&.-]+\s+){1,4}?)strategy\b",
        text,
        flags=re.IGNORECASE,
    )
    if strategy_subject:
        for token in strategy_subject.group(1).split():
            candidate = _clean_symbol(token)
            if candidate and candidate not in _SYMBOL_STOPWORDS and not candidate.isdigit():
                return candidate
    for pattern in (
        r"\b(?:symbol|ticker|underlying|instrument)\s*[:=]?\s*"
        r"([A-Za-z][A-Za-z0-9&.-]{1,30})\b",
        r"\b(?:for|on)\s+([A-Za-z][A-Za-z0-9&.-]{1,30})\b",
        r"\b(?:buy|sell|short|trade)\s+([A-Za-z][A-Za-z0-9&.-]{1,30})\b",
    ):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            candidate = _clean_symbol(match.group(1))
            if candidate and candidate not in _SYMBOL_STOPWORDS:
                return candidate
    # Fall back to explicit all-caps tickers typed by the user.
    for match in re.finditer(r"\b[A-Z][A-Z0-9&.-]{1,30}\b", text):
        candidate = _clean_symbol(match.group(0))
        if candidate and candidate not in _SYMBOL_STOPWORDS:
            return candidate
    return None


def _clean_symbol(value: str) -> str:
    return (
        value.strip().strip(".,;:)]}").replace("&", "").replace(".", "").upper()
    )


def _strategy_name(
    symbol: str,
    state: _CompileState,
    text: str,
) -> str:
    named = re.search(
        r"\b(?:called|named)\s+([A-Za-z][A-Za-z0-9_-]{1,60})\b",
        text,
        flags=re.IGNORECASE,
    )
    if named:
        return named.group(1).lower()
    labels = list(dict.fromkeys(state.labels)) or ["rules"]
    return f"{symbol.lower()}_{'_'.join(labels[:3])}_strategy"


def _explicit_feature_inputs(
    lower: str,
    external_refs: set[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for feature_name in sorted(external_refs):
        dataset = re.search(
            r"\b(?:feature(?:\s+(?:series|data))?\s+dataset|dataset)"
            r"\s*(?:is|=|:)?\s*([A-Za-z][A-Za-z0-9_-]{0,159})\b",
            lower,
        )
        dataset_id = dataset.group(1) if dataset else ""
        if not dataset_id:
            warnings.append(
                f"The rule references external feature '{feature_name}'. "
                "Import a governed point-in-time feature dataset and set its "
                "dataset_id in feature_inputs before this strategy can run."
            )
        inputs.append(
            {
                "name": feature_name,
                "dataset_id": dataset_id,
                "feature_name": feature_name,
                "alignment": "asof",
                "max_age_hours": (
                    1.0
                    if feature_name in {"iv_skew", "open_interest"}
                    else 24.0
                ),
            }
        )
    return inputs
