"""Tool results in, plain English out.

Every renderer here takes a real tool payload and describes exactly what is
in it. None of them invent a number, soften a loss, or fill a missing field —
when data is absent the renderer says so, because a confident sentence over
absent data is the most damaging thing this layer could produce.

Split out of ``orchestration`` because it is the largest concern in it and
the most independent: rendering needs no routing, and routing needs only
``_grounded_fallback_response`` back.
"""

from __future__ import annotations

import re
from typing import Any

from ..services.instrument_names import company_name as _company_name


def _pending_order_summary(approval: dict[str, Any]) -> str:
    """Plain one-line summary of a pending order, e.g. 'BUY 10 RELIANCE'."""
    side = approval.get("side")
    quantity = approval.get("quantity")
    symbol = approval.get("symbol")
    if side and quantity and symbol:
        order_type = str(approval.get("order_type") or "MARKET").lower()
        return f"{side} {quantity} {symbol} ({order_type})"
    action = str(approval.get("requested_action") or "order").replace("_", " ")
    return action


def grounded_tool_response(tool_name: str, result: dict[str, Any]) -> str:
    return _grounded_fallback_response(tool_name, result)


def _pick(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _name_suffix(symbol: Any, row: dict[str, Any]) -> str:
    """' (Reliance Industries)' for a ticker, or '' when unknown."""
    name = row.get("company_name") or _company_name(
        str(symbol) if symbol else None,
        row.get("exchange") or "NSE",
    )
    return f" ({name})" if name else ""


_WALK_FORWARD_LABELS = {
    "holds_up": "✅ Holds up — it kept most of its edge on unseen data.",
    "weaker_but_positive": "🟡 Weaker but still positive out-of-sample.",
    "overfit": "❌ Overfit — profitable in-sample, but lost money on unseen data.",
    "poor": "❌ Poor — it lost money on the unseen test window.",
    "inconclusive": "⚠️ Inconclusive — too few out-of-sample trades to judge.",
}


def _render_walk_forward_result(result: dict[str, Any]) -> str:
    """Deterministic in-sample vs out-of-sample report."""
    strategy = str(result.get("strategy", "")).replace("_", " ")
    if result.get("status") != "ok":
        return (
            f"I couldn't run a walk-forward check on the {strategy} strategy — "
            "there wasn't a usable configuration over the stored history."
        )
    params = ", ".join(
        f"{k}={v}" for k, v in result.get("parameters", {}).items()
    )
    verdict = _WALK_FORWARD_LABELS.get(
        result.get("verdict", ""), result.get("verdict", "")
    )
    return "\n".join(
        [
            f"**Walk-forward check — {strategy}**",
            "",
            f"I picked the best config on the older {result.get('train_bars')} "
            f"bars, then tested it on the newer {result.get('test_bars')} bars "
            "it had never seen.",
            "",
            f"- Config: {params}",
            f"- In-sample (train): **{result.get('in_sample_return_pct')}%** "
            f"over {result.get('in_sample_trades')} trades",
            f"- Out-of-sample (test): **{result.get('out_of_sample_return_pct')}%** "
            f"over {result.get('out_of_sample_trades')} trades "
            f"(drawdown ₹{result.get('out_of_sample_drawdown')})",
            "",
            f"**Verdict:** {verdict}",
            "",
            "_Historical validation only — not a prediction of future returns or "
            "investment advice._",
        ]
    )


def _render_optimization_result(result: dict[str, Any]) -> str:
    """Deterministic leaderboard for the strategy-optimizer agent."""
    strategy = str(result.get("strategy", "")).replace("_", " ")
    rows = [r for r in result.get("results", []) if r.get("return_pct") is not None]
    best = result.get("best")
    if not rows:
        errs = [r.get("error") for r in result.get("results", []) if r.get("error")]
        reason = errs[0] if errs else "no usable backtests"
        return (
            f"I couldn't optimise the {strategy} strategy — {reason}."
        )
    lines = [
        f"I backtested {result.get('candidates_tried', 0)} {strategy} "
        f"configurations over your stored history. Ranked by return:",
        "",
    ]
    for row in rows[:8]:
        params = row.get("parameters", {})
        param_str = ", ".join(f"{k}={v}" for k, v in params.items())
        flag = "" if row.get("reliable") else " _(too few trades)_"
        lines.append(
            f"- **{row.get('return_pct')}%** · P&L ₹{row.get('net_pnl')} · "
            f"drawdown ₹{row.get('max_drawdown')} · "
            f"{row.get('total_trades')} trades — {param_str}{flag}"
        )
    if best:
        bp = ", ".join(f"{k}={v}" for k, v in best.get("parameters", {}).items())
        lines.append("")
        note = (
            " (best available, but it had few trades — treat as weak evidence)"
            if result.get("used_unreliable_best")
            else ""
        )
        lines.append(
            f"**Best configuration{note}:** {bp} → {best.get('return_pct')}% "
            f"return, {best.get('total_trades')} trades."
        )
    lines.append("")
    lines.append(
        "_Historical backtest results only — past performance doesn't predict "
        "future returns, and this isn't investment advice. Ask me to save the "
        "best one as a strategy to trade it (with your approval)._"
    )
    return "\n".join(lines)


def _render_remember_result(result: dict[str, Any]) -> str:
    content = result.get("content", "")
    return (
        f"Got it — I'll remember that: “{content}”.\n\n"
        "Ask “what do you remember” any time to see everything on file."
    )


def _render_recall_result(result: dict[str, Any]) -> str:
    notes = result.get("notes", [])
    research = result.get("research")
    lines: list[str] = []
    if notes:
        lines.append("**What I remember:**")
        for note in notes:
            when = _short_date(note.get("created_at"))
            suffix = f" — noted {when}" if when else ""
            lines.append(f"- {note.get('content')}{suffix}")
    else:
        lines.append(
            "I don't have any saved notes yet. Tell me “remember that ...” "
            "and I'll keep it."
        )
    if research:
        when = _short_date(research.get("updated_at"))
        lines.append("")
        lines.append(
            f"**Last research on {research.get('symbol')}"
            f"{f' ({when})' if when else ''}:** {research.get('content')}"
        )
    return "\n".join(lines)


def _short_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    return text[:10] if len(text) >= 10 else text


def _watch_condition_text(condition: str | None, threshold: Any) -> str:
    mapping = {
        "rsi_below": f"RSI below {threshold}",
        "rsi_above": f"RSI above {threshold}",
        "price_above_ema20": "price crossing above its EMA20",
        "price_below_ema20": "price crossing below its EMA20",
    }
    return mapping.get(condition or "", str(condition))


def _render_watch_list(result: dict[str, Any]) -> str:
    watches = result.get("watches", [])
    if not watches:
        return (
            "You have no technical watches yet. Try “watch RELIANCE for RSI "
            "below 30”."
        )
    lines = ["**Your technical watches:**"]
    for w in watches:
        cond = _watch_condition_text(w.get("condition"), w.get("threshold"))
        status = w.get("status")
        tail = ""
        if w.get("last_value") is not None:
            tail = f" — last read {w['last_value']}"
        lines.append(f"- **{w.get('symbol')}**: {cond} · {status}{tail}")
    return "\n".join(lines)


def _render_watch_check(result: dict[str, Any]) -> str:
    checked = result.get("checked", 0)
    fired = result.get("fired", [])
    errors = result.get("errors", [])
    if checked == 0 and not fired:
        return (
            "There are no active watches to check. Add one with “watch RELIANCE "
            "for RSI below 30”."
        )
    lines = [f"Checked {checked} active watch(es)."]
    if fired:
        lines.append("")
        lines.append("**Fired:**")
        for w in fired:
            cond = _watch_condition_text(w.get("condition"), w.get("threshold"))
            lines.append(
                f"- **{w.get('symbol')}**: {cond} (now {w.get('last_value')})"
            )
    else:
        lines.append("None have fired yet.")
    if errors:
        lines.append("")
        lines.append("_Couldn't check: " + "; ".join(errors[:5]) + "._")
    return "\n".join(lines)


def _render_comparison_result(result: dict[str, Any]) -> str:
    """Deterministic side-by-side comparison from the plan-and-execute agent."""
    symbols = result.get("symbols", [])
    lines = [f"**Comparing {' vs '.join(symbols)}**", ""]
    comparison = result.get("comparison", [])
    if comparison:
        for row in comparison:
            metric = str(row.get("metric", "")).replace("_", " ")
            values = row.get("values", {})
            shown = " · ".join(f"{sym} {values.get(sym)}" for sym in symbols)
            lines.append(
                f"- **{metric}** ({row.get('direction')} is better): {shown} "
                f"→ {row.get('better')}"
            )
        lines.append("")
    leader = result.get("fundamental_leader")
    if leader:
        lines.append(f"**On the fundamentals compared, {leader} leads.**")
    for note in result.get("notes", []):
        lines.append(f"- {note}")
    lines.append("")
    lines.append(
        "_A factual comparison of available data, not a buy/sell recommendation._"
    )
    return "\n".join(lines)


def _render_research_briefing(result: dict[str, Any]) -> str:
    """Deterministic multi-analyst briefing from gathered findings.

    Used when no LLM is configured; otherwise the LLM composes a thesis from
    the same structured findings. Only reports real data — unavailable
    specialists are stated plainly.
    """
    symbol = result.get("symbol", "?")
    name = result.get("company_name", symbol)
    lines = [f"# {name} ({symbol}) — research briefing", ""]

    valuation = result.get("valuation", {})
    if valuation.get("available"):
        ltp = valuation.get("ltp")
        rng = []
        if valuation.get("open") is not None:
            rng.append(f"open ₹{valuation['open']}")
        if valuation.get("high") is not None and valuation.get("low") is not None:
            rng.append(f"day ₹{valuation['low']}–₹{valuation['high']}")
        lines.append(
            f"**Price:** ₹{ltp}" + (f" · {' · '.join(rng)}" if rng else "")
        )
    else:
        lines.append(
            f"**Price:** live quote unavailable ({valuation.get('reason', 'n/a')})."
        )

    technicals = result.get("technicals", {})
    if technicals.get("available"):
        lines.append(
            f"**Technicals:** {technicals.get('trend')} · RSI "
            f"{technicals.get('rsi')} ({technicals.get('momentum')}) · "
            f"EMA20 ₹{technicals.get('ema20')} vs EMA50 ₹{technicals.get('ema50')}."
        )
    else:
        lines.append(
            f"**Technicals:** unavailable ({technicals.get('reason', 'n/a')})."
        )

    fundamentals = result.get("fundamentals", {})
    if fundamentals.get("available"):
        ratios = fundamentals.get("ratios", {})
        shown = ", ".join(
            f"{key.replace('_', ' ')} {value}"
            for key, value in list(ratios.items())[:6]
        )
        period = fundamentals.get("period")
        lines.append(
            "**Fundamentals**"
            + (f" ({period})" if period else "")
            + f": {shown}."
        )
    else:
        lines.append(
            "**Fundamentals:** none imported yet — add statements in the Data "
            "tab to include them."
        )

    news = result.get("news", {})
    if news.get("available"):
        lines.append("**Recent news:**")
        for headline in news.get("headlines", [])[:5]:
            source = headline.get("source") or "unknown"
            lines.append(f"- {headline.get('title')} ({source})")
    else:
        lines.append(
            f"**Recent news:** unavailable ({news.get('reason', 'n/a')})."
        )

    gaps = result.get("gaps", [])
    if gaps:
        lines.append("")
        lines.append(
            "_This briefing summarises the data available now; missing pieces: "
            + "; ".join(gap.split(":")[0] for gap in gaps)
            + "._"
        )
    lines.append("")
    lines.append(
        "_Balanced summary of real data, not investment advice._"
    )
    return "\n".join(lines)


def _render_research_report(result: dict[str, Any]) -> str:
    """Deterministic cited report from the iterative deep-research loop.

    Reuses the briefing for the core findings, then shows the agent's own
    coverage self-critique, any deepening reading it pulled, and a sources list
    so every claim is traceable. LLM composition (when configured) works from
    the same structured bundle; nothing here is fabricated.
    """
    findings = result.get("findings", {})
    briefing = _render_research_briefing(
        {
            "symbol": result.get("symbol"),
            "company_name": result.get("company_name"),
            **findings,
            "gaps": result.get("gaps", []),
        }
    )
    # Retitle from "briefing" to "research report".
    briefing = briefing.replace("— research briefing", "— research report", 1)
    lines = [briefing, ""]

    critique = result.get("self_critique", [])
    if critique:
        lines.append(
            f"**How I researched this** ({result.get('iterations', 1)} pass"
            f"{'es' if result.get('iterations', 1) != 1 else ''}):"
        )
        for note in critique:
            lines.append(f"- {note}")
        lines.append("")

    web = result.get("web_research", [])
    if web:
        lines.append("**Further reading pulled in:**")
        for doc in web:
            title = doc.get("title") or "web document"
            url = doc.get("url")
            lines.append(f"- {title}" + (f" — {url}" if url else ""))
        lines.append("")

    citations = result.get("citations", [])
    if citations:
        lines.append("**Sources:**")
        for cite in citations:
            ref = cite.get("ref")
            url = cite.get("url")
            tail = url or ref
            lines.append(f"- {cite.get('source')}" + (f" ({tail})" if tail else ""))
    return "\n".join(lines).rstrip()


def _render_account_snapshot(result: dict[str, Any]) -> str:
    snapshot_type = result.get("snapshot_type", "account")
    data = result.get("data")

    if snapshot_type == "funds" and isinstance(data, dict):
        cash = _num(_pick(data, "availablecash", "available_cash", "cash"))
        unrealized = _num(_pick(data, "m2munrealized", "unrealized_pnl"))
        realized = _num(_pick(data, "m2mrealized", "realized_pnl"))
        used = _num(_pick(data, "utiliseddebits", "utilised_margin", "used_margin"))
        lines = []
        if cash is not None:
            lines.append(f"- **Available cash**: ₹{cash:,.2f}")
        if used is not None:
            lines.append(f"- **Used margin**: ₹{used:,.2f}")
        if unrealized is not None:
            lines.append(f"- **Unrealized P&L**: ₹{unrealized:,.2f}")
        if realized is not None:
            lines.append(f"- **Realized P&L**: ₹{realized:,.2f}")
        return "**Your account**\n" + ("\n".join(lines) or "- No balance details returned.")

    rows = data if isinstance(data, list) else []

    if snapshot_type in {"positionbook", "holdings"}:
        if not rows:
            return "You have no open positions right now."
        lines = []
        total_pnl = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = _pick(row, "symbol", "tradingsymbol", "tsym")
            qty = _num(_pick(row, "quantity", "netqty", "qty", "netQuantity"))
            avg = _num(_pick(row, "average_price", "averageprice", "avgprice", "buyavgprice"))
            ltp = _num(_pick(row, "ltp", "lastprice", "last_price"))
            pnl = _num(_pick(row, "pnl", "unrealized_pnl", "m2m", "profitandloss"))
            if pnl is None and None not in (qty, avg, ltp):
                pnl = (ltp - avg) * qty
            if pnl is not None:
                total_pnl += pnl
            parts = [f"**{sym}**{_name_suffix(sym, row)}"]
            if qty is not None:
                parts.append(f"qty {qty:g}")
            if avg is not None:
                parts.append(f"avg ₹{avg:,.2f}")
            if ltp is not None:
                parts.append(f"LTP ₹{ltp:,.2f}")
            if pnl is not None:
                parts.append(f"P&L ₹{pnl:,.2f}")
            lines.append("- " + " · ".join(parts))
        header = "**Your holdings**" if snapshot_type == "holdings" else "**Your open positions**"
        return (
            f"{header}\n" + "\n".join(lines)
            + f"\n\n**Total P&L: ₹{total_pnl:,.2f}**"
        )

    if snapshot_type == "orderbook":
        if not rows:
            return "You have no orders today."
        lines = []
        for row in rows[:15]:
            if not isinstance(row, dict):
                continue
            sym = _pick(row, "symbol", "tradingsymbol", "tsym")
            side = _pick(row, "action", "transaction_type", "side")
            qty = _num(_pick(row, "quantity", "qty"))
            price = _num(_pick(row, "price", "average_price", "averageprice"))
            status = _pick(row, "order_status", "status", "orderstatus")
            parts = [f"**{sym}**{_name_suffix(sym, row)}", str(side or "")]
            if qty is not None:
                parts.append(f"qty {qty:g}")
            if price is not None:
                parts.append(f"₹{price:,.2f}")
            if status:
                parts.append(str(status))
            lines.append("- " + " · ".join(p for p in parts if p))
        return "**Your orders**\n" + "\n".join(lines)

    if snapshot_type == "tradebook":
        if not rows:
            return "You have no trades today."
        lines = []
        for row in rows[:15]:
            if not isinstance(row, dict):
                continue
            sym = _pick(row, "symbol", "tradingsymbol", "tsym")
            side = _pick(row, "action", "transaction_type", "side")
            qty = _num(_pick(row, "quantity", "qty", "fillsize"))
            price = _num(_pick(row, "average_price", "averageprice", "price", "fillprice"))
            parts = [f"**{sym}**{_name_suffix(sym, row)}", str(side or "")]
            if qty is not None:
                parts.append(f"qty {qty:g}")
            if price is not None:
                parts.append(f"₹{price:,.2f}")
            lines.append("- " + " · ".join(p for p in parts if p))
        return "**Your trades today**\n" + "\n".join(lines)

    if isinstance(data, list):
        return "No records returned." if not data else (
            "**Account snapshot**\n"
            + "\n".join(f"- {row}" for row in data[:10] if isinstance(row, (str, int, float)))
        )
    return "Account snapshot retrieved."


def _grounded_fallback_response(
    tool_name: str,
    result: dict[str, Any],
) -> str:
    if tool_name == "list_datasets":
        datasets = result.get("datasets", [])
        if not datasets:
            return "No governed datasets were found."
        dataset_ids = ", ".join(
            str(dataset.get("dataset_id"))
            for dataset in datasets
        )
        return (
            f"Found {len(datasets)} governed dataset(s): {dataset_ids}."
        )
    if tool_name == "list_strategies":
        return (
            f"Registered {len(result.get('strategies', []))} deterministic "
            "strategy plugins."
        )
    if tool_name == "get_platform_summary":
        counts = result.get("counts", {})
        execution_paths = result.get("execution_paths", {})
        enabled_paths = [
            name
            for name, path in execution_paths.items()
            if path.get("enabled")
        ]
        return (
            f"Platform status is {result.get('status')}. Governed datasets: "
            f"{counts.get('data_catalog', 0)}; completed strategy runs: "
            f"{counts.get('strategy_runs', 0)}. Enabled execution paths: "
            f"{', '.join(enabled_paths) or 'none'}. Live trading enabled: "
            f"{result.get('safety', {}).get('live_trading_enabled')}. "
            ""
        )
    if tool_name == "run_screen":
        matches = result.get("matches", [])
        excluded = result.get("excluded", [])
        criteria_text = "; ".join(
            f"{item['metric']} {item['op']} {item['value']}"
            for item in result.get("criteria", [])
        )
        match_lines = [
            f"- **{item['symbol']}**: "
            + ", ".join(
                f"{metric}={value:.4f}" if value is not None else f"{metric}=n/a"
                for metric, value in item["values"].items()
            )
            for item in matches
        ] or ["- (no symbols passed)"]
        excluded_note = (
            f"\n{len(excluded)} symbol(s) excluded (failed criteria or "
            "missing metrics)."
            if excluded
            else ""
        )
        return (
            f"Screen **{result.get('screen')}** v{result.get('version')} "
            f"({criteria_text}) over {result.get('universe_size', 0)} "
            f"symbol(s) with imported statements:\n"
            + "\n".join(match_lines)
            + excluded_note
            + "\nScreens evaluate deterministic ratios from imported "
            "statements only; import more statements to widen the universe."
        )
    if tool_name == "analyze_fundamentals":
        lines = []
        for item in result.get("ratios", []):
            value = item.get("value")
            rendered = f"{value:,.4f}".rstrip("0").rstrip(".") if value is not None else "n/a"
            lines.append(
                f"- **{item['name']}** = {rendered} "
                f"({item['formula']})"
            )
        warnings = result.get("warnings", [])
        warning_text = (
            "\n\nWarnings:\n" + "\n".join(f"- {w}" for w in warnings)
            if warnings
            else ""
        )
        return (
            f"Fundamental analysis for **{result.get('symbol')}** "
            f"(latest period {result.get('latest_period')}, "
            f"{result.get('currency')}, source: {result.get('source')}; "
            f"periods stored: {', '.join(result.get('periods_available', []))}):\n"
            + "\n".join(lines)
            + warning_text
        )
    if tool_name == "mark_portfolio_to_market":
        marked = result.get("positions_marked", [])
        lines = [
            f"- **{item['symbol']}**: {item['quantity']:g} @ avg "
            f"{item['average_price']:g}, live {item['live_price']:g} → "
            f"unrealized {item['unrealized_pnl']:+,.2f}"
            for item in marked
        ] or ["- (no open positions)"]
        error_note = (
            f"\n{len(result.get('quote_errors', []))} position(s) could not "
            "be quoted."
            if result.get("quote_errors")
            else ""
        )
        return (
            f"**{result.get('name')}** ({result.get('portfolio_id')}) "
            "marked to live quotes:\n"
            + "\n".join(lines)
            + f"\n\nCash: {result.get('cash_balance'):,.2f} · Market value: "
            f"{result.get('market_value'):,.2f} · Total equity: "
            f"{result.get('total_equity'):,.2f} · Unrealized P&L: "
            f"{result.get('total_unrealized_pnl'):+,.2f}"
            + error_note
        )
    if tool_name in {"add_watchlist_symbol", "remove_watchlist_symbol"}:
        return (
            f"Watchlist updated: **{result.get('symbol')}** "
            f"{result.get('status')}."
        )
    if tool_name == "create_watch":
        condition = _watch_condition_text(
            result.get("condition"), result.get("threshold")
        )
        # Say plainly when the watch already existed. Claiming to have set up
        # something that was already there invites the user to ask again, and
        # each repeat used to add another alert that would fire alongside it.
        if result.get("already_watching"):
            return (
                f"You're already watching **{result.get('symbol')}** for "
                f"{condition}, so I've left that one in place rather than "
                "adding a second alert for the same thing."
            )
        return (
            f"Now watching **{result.get('symbol')}** for {condition}. "
            "I'll flag it when it fires — say “check my watches” any time. "
            "(A watch only notifies; it never trades.)"
        )
    if tool_name == "remove_watch":
        return f"Stopped watching **{result.get('symbol')}**."
    if tool_name == "list_watches":
        return _render_watch_list(result)
    if tool_name == "check_watches":
        return _render_watch_check(result)
    if tool_name == "list_watchlist":
        symbols = result.get("symbols", [])
        if not symbols:
            return (
                "The watchlist is empty. Say 'add RELIANCE to watchlist' "
                "to start building the screening universe."
            )
        listed = ", ".join(
            f"{item['symbol']} ({item['exchange']})" for item in symbols
        )
        return f"Watchlist ({len(symbols)}): {listed}."
    if tool_name == "run_technical_screen":
        matches = result.get("matches", [])
        skipped = result.get("skipped", [])
        condition = str(result.get("condition", "")).replace("_", " ")
        universe = result.get("universe", "watchlist")
        universe_label = (
            "NIFTY 50" if universe == "nifty50" else "your watchlist"
        )
        scanned = result.get("universe_size", result.get("watchlist_size", 0))
        match_lines = [
            f"- **{item['symbol']}**"
            + (f" ({item['company_name']})" if item.get("company_name") else "")
            + f": close ₹{item.get('last_close')}"
            + (f" · RSI {item['rsi']}" if "rsi" in item else "")
            + (f" · EMA {item['ema']}" if "ema" in item else "")
            + (f" · volume {item['volume']}" if "volume" in item else "")
            for item in matches
        ]
        skipped_note = (
            f"\n\n_{len(skipped)} of {scanned} couldn't be checked "
            "(no data returned)._"
            if skipped
            else ""
        )
        if not matches:
            return (
                f"I scanned {scanned} {universe_label} stock(s) for "
                f"**{condition} {result.get('threshold')}** on "
                f"{result.get('interval')} candles — **none matched** right "
                f"now.{skipped_note}"
            )
        return (
            f"Scanned {scanned} {universe_label} stock(s) for "
            f"**{condition} {result.get('threshold')}** "
            f"({result.get('interval')} candles). "
            f"**{len(matches)} match(es):**\n"
            + "\n".join(match_lines)
            + skipped_note
        )
    if tool_name == "create_price_alert":
        return (
            f"Price alert created: **{result.get('symbol')}** "
            f"{result.get('direction')} {result.get('threshold')} "
            f"({result.get('exchange')}). It is checked against live "
            "quotes every minute while the broker connection is up; say "
            "'show my alerts' to review it."
        )
    if tool_name == "list_price_alerts":
        alerts = result.get("alerts", [])
        if not alerts:
            return (
                "No price alerts yet. Say 'alert me when RELIANCE goes "
                "above 1500' to create one."
            )
        lines = [
            f"- **{item['symbol']}** {item['direction']} {item['threshold']} "
            f"— {item['status']}"
            + (
                f" (last price {item['last_price']})"
                if item.get("last_price") is not None
                else ""
            )
            for item in alerts[:15]
        ]
        return "Your price alerts:\n" + "\n".join(lines)
    if tool_name == "get_option_chain":
        analytics = result.get("analytics", {})
        pcr = analytics.get("put_call_oi_ratio")
        return (
            f"**{result.get('underlying')} option chain** "
            f"(expiry {result.get('expiry_date')}, "
            f"spot {result.get('underlying_ltp')}):\n"
            f"- ATM strike: {analytics.get('atm_strike')} "
            f"(call {analytics.get('atm_call_ltp')}, "
            f"put {analytics.get('atm_put_ltp')})\n"
            f"- ATM straddle cost: {analytics.get('atm_straddle_cost')}\n"
            f"- Put-call OI ratio: {pcr}\n"
            f"- Max call OI at {analytics.get('max_call_oi_strike')}; "
            f"max put OI at {analytics.get('max_put_oi_strike')}\n"
            f"- Strikes returned: {len(result.get('strike_rows', []))}"
        )
    if tool_name == "fetch_web_document":
        return (
            f"Saved **{result.get('title')}** from {result.get('source_url')} "
            f"into the document corpus ({result.get('chunk_count', 0)} "
            "chunk(s)).\n\n"
            f"You can now ask: 'analyze document {result.get('title')}' or "
            "'search knowledge <topic>'."
        )
    if tool_name == "run_strategy_optimization":
        return _render_optimization_result(result)
    if tool_name == "validate_strategy_walk_forward":
        return _render_walk_forward_result(result)
    if tool_name == "deep_research":
        return _render_research_briefing(result)
    if tool_name == "deep_research_report":
        return _render_research_report(result)
    if tool_name == "compare_investments":
        return _render_comparison_result(result)
    if tool_name == "remember":
        return _render_remember_result(result)
    if tool_name == "recall_memory":
        return _render_recall_result(result)
    if tool_name == "find_and_analyze_document":
        chunks = result.get("chunks", [])
        excerpt_lines = []
        for chunk in chunks:
            content = " ".join(str(chunk.get("content", "")).split())
            if len(content) > 320:
                content = content[:320].rstrip() + "…"
            excerpt_lines.append(f"{chunk.get('chunk_index', 0) + 1}. {content}")
        excerpts = "\n".join(excerpt_lines) or "(no readable text)"
        if result.get("source") == "web_fetched":
            source = (
                f"I fetched **{result.get('title')}** from "
                f"{result.get('source_url')} and indexed it"
            )
        else:
            source = f"From your stored document **{result.get('title')}**"
        return (
            f"{source} ({result.get('chunk_count', 0)} section(s), "
            f"{result.get('total_words', 0)} words).\n\n"
            f"Here is what it covers:\n{excerpts}\n\n"
            "Ask a follow-up and I'll answer from this document."
        )
    if tool_name == "analyze_knowledge_document":
        chunks = result.get("chunks", [])
        excerpt_lines = []
        for chunk in chunks:
            content = " ".join(str(chunk.get("content", "")).split())
            if len(content) > 320:
                content = content[:320].rstrip() + "…"
            excerpt_lines.append(f"{chunk.get('chunk_index', 0) + 1}. {content}")
        excerpts = "\n".join(excerpt_lines) or "(no stored text)"
        return (
            f"**{result.get('title')}** ({result.get('document_id')}, "
            f"{result.get('document_type')}) — "
            f"{result.get('chunk_count', 0)} stored chunk(s), "
            f"{result.get('total_words', 0)} words.\n\n"
            f"Key excerpts in document order:\n{excerpts}\n\n"
            "Ask 'search knowledge <topic>' to pull specific passages."
        )
    if tool_name == "search_knowledge":
        matches = result.get("matches", [])
        if not matches:
            return (
                "I couldn't find anything about that in your stored "
                "documents. You can upload reports in the Data tab or say "
                "'fetch <url> and store it'."
            )
        excerpts = []
        for item in matches[:3]:
            content = " ".join(str(item.get("content", "")).split())
            if len(content) > 260:
                content = content[:260].rstrip() + "…"
            excerpts.append(f"- **{item['title']}**: {content}")
        return (
            "Here's what your documents say:\n" + "\n".join(excerpts)
        )
    if tool_name == "check_platform_readiness":
        blocked_reasons = []
        if not result.get("supported_by_architecture", False):
            blocked_reasons.append(result.get("unsupported_reason") or "unsupported asset or symbol")
        if not result.get("local_dataset_exists", False):
            blocked_reasons.append("local historical dataset is missing")
        if result.get("analyzer_path_status") not in {None, "ready", "available"}:
            blocked_reasons.append(f"OpenAlgo analyzer path is {result.get('analyzer_path_status')}")
        if result.get("unsupported_reason"):
            blocked_reasons.append(result["unsupported_reason"])
        unique_blockers = []
        for blocker in blocked_reasons:
            if blocker and blocker not in unique_blockers:
                unique_blockers.append(blocker)
        return (
            f"Readiness for {result['symbol']} {result['asset_class']} "
            f"completed. Local dataset: {result['local_dataset_exists']}; "
            f"rows available: {result.get('rows_available', 0)}; "
            f"provider configured: {result['provider_configured']}; "
            f"verified now: {result['verified_now']}; analyzer path: "
            f"{result.get('analyzer_path_status')}; paper path: "
            f"{result.get('paper_path_status')}; live path: "
            f"{result.get('live_path_status')}. Blockers: "
            f"{'; '.join(unique_blockers) if unique_blockers else 'none'}. "
            ""
        )
    if tool_name == "get_research_context":
        readiness = result["readiness"]
        news = result["news"]
        return (
            f"Research context for {readiness['symbol']} "
            f"{readiness['asset_class']} is ready at the architecture level: "
            f"{readiness['supported_by_architecture']}. Local dataset: "
            f"{readiness['local_dataset_exists']} with "
            f"{readiness['rows_available']} row(s). Stored news articles: "
            f"{len(news.get('articles', []))}."
        )
    if tool_name == "create_research_brief":
        actions = result.get("next_actions", [])
        return (
            f"Created research brief {result['brief_id']} for "
            f"{result['symbol']} {result['asset_class']}. "
            f"Evidence dataset: {result.get('evidence', {}).get('dataset_id') or 'none'}; "
            f"next action: {actions[0] if actions else 'none'}. "
            ""
        )
    if tool_name == "get_execution_readiness":
        stages = result.get("stages", [])
        ready = [
            stage["stage"]
            for stage in stages
            if stage.get("can_start")
        ]
        blocker = result.get("next_blocker")
        summary = (
            f"Execution readiness for {result['symbol']} "
            f"{result['asset_class']} checked. Ready stages: "
            f"{', '.join(ready) or 'none'}. Next blocker: "
            f"{blocker['stage'] if blocker else 'none'}. "
            ""
        )
        data = result.get("data_readiness", {})
        paper_signal = result.get("paper_signal", {})
        if not paper_signal.get("eligible"):
            return (
                f"{summary} A current risk-approved paper signal is still "
                "required before any OpenAlgo analyzer order can be prepared. "
                f"{paper_signal.get('next_action', 'Refresh data and run the named strategy again.')}"
            )
        if (
            not data.get("local_dataset_exists")
            and data.get("historical_available")
        ):
            return (
                f"{summary} OpenAlgo has verified historical data for this "
                "instrument, but it has not been imported into the governed "
                "catalog yet. Ask me to import its historical data, then name "
                "the strategy for a semi-auto backtest."
            )
        return summary
    if tool_name == "get_openalgo_monitor":
        return (
            f"OpenAlgo monitor status: {result['status']}. "
            f"Configured: {result['configured']}; live trading enabled: "
            f"{result['live_trading_enabled']}."
        )
    if tool_name == "get_openalgo_snapshot":
        return _render_account_snapshot(result)
    if tool_name == "search_instruments":
        return (
            f"OpenAlgo instrument search status: {result.get('status')}. "
            f"Matches: {result.get('match_count', 0)}. "
            ""
        )
    if tool_name == "validate_instrument_symbol":
        instrument = result.get("instrument", {})
        return (
            f"Symbol validation status: {result.get('status')}. "
            f"Resolved: {instrument.get('symbol', result.get('symbol'))} "
            f"on {instrument.get('exchange', result.get('exchange'))}. "
            ""
        )
    if tool_name == "resolve_option_symbol":
        return (
            f"Option symbol resolution status: {result.get('status')}. "
            f"Resolved: {result.get('resolved_symbol')} on "
            f"{result.get('resolved_exchange')}. "
            ""
        )
    if tool_name == "get_market_news":
        if not result.get("ok"):
            return (
                f"Market news unavailable: {result.get('message')}. "
                ""
            )
        articles = result.get("articles", [])
        if not articles:
            subject = result.get("symbol") or result.get("query") or "this request"
            return (
                f"The configured news provider returned no matching articles "
                f"for {subject}. No market outlook was inferred from missing "
                "news."
            )
        unique_headlines: list[str] = []
        seen_titles: set[str] = set()
        for item in articles:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Untitled").strip()
            normalized_title = re.sub(r"\s+", " ", title.lower())
            if normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)
            unique_headlines.append(
                f"- **{title}** — {item.get('source', 'source unavailable')}"
            )
            if len(unique_headlines) == 5:
                break
        subject = result.get("symbol") or result.get("query") or "the market"
        return (
            f"**Latest headlines for {subject}**\n"
            + "\n".join(unique_headlines)
        )
    if tool_name == "get_market_quote":
        if not result.get("ok"):
            return (
                f"Market quote unavailable: "
                f"{str(result.get('message') or '').rstrip('.')}. "
                ""
            )
        quote = result.get("quote", {})
        fields = [
            f"{field}={quote[field]}"
            for field in (
                "ltp",
                "last_price",
                "close",
                "open",
                "high",
                "low",
                "volume",
                "timestamp",
            )
            if field in quote
        ]
        return (
            f"Provider-backed quote for {result.get('resolved_symbol')} on "
            f"{result.get('resolved_exchange')}: "
            f"{', '.join(fields) or 'no quote fields returned'}."
            + (
                " Resolved from the local OpenAlgo master contract."
                if result.get("instrument_resolution")
                == "local_contract_fuzzy_match"
                else ""
            )
        )
    if tool_name == "list_strategy_personas":
        personas = result.get("personas", [])
        if not personas:
            return "No governed strategy personas are configured."
        lines = []
        for item in personas:
            preferred = ", ".join(
                item.get("strategy_bias", {}).get("preferred_strategies", [])
            ) or "none"
            lines.append(
                f"- **{item.get('name')}** ({item.get('persona_id')}): "
                f"{item.get('description')} Preferred strategies: "
                f"{preferred}."
            )
        return (
            f"Found {len(personas)} governed strategy persona(s):\n"
            + "\n".join(lines)
            + "\n\nAsk about one by name, for example 'show the "
            "conservative value persona'."
        )
    if tool_name == "get_strategy_persona":
        persona = result.get("persona", {})
        bias = persona.get("strategy_bias", {})
        risk_rules = persona.get("risk_rules", {})
        preferred = ", ".join(bias.get("preferred_strategies", [])) or "none"
        risk_lines = []
        if risk_rules.get("max_order_value") is not None:
            risk_lines.append(
                f"max order value {float(risk_rules['max_order_value']):,.0f}"
            )
        if risk_rules.get("stop_loss_pct") is not None:
            risk_lines.append(
                f"stop loss {float(risk_rules['stop_loss_pct']) * 100:g}%"
            )
        if risk_rules.get("requires_approval_for_paper"):
            risk_lines.append("paper orders need human approval")
        if risk_rules.get("requires_approval_for_live"):
            risk_lines.append("live orders need human approval")
        focus = ", ".join(persona.get("dashboard_focus", [])) or "none"
        first_strategy = (
            bias.get("preferred_strategies", ["sma_crossover"]) or
            ["sma_crossover"]
        )[0]
        sections = [
            f"**{persona.get('name')}** ({persona.get('persona_id')}): "
            f"{persona.get('description')}",
            f"- **Asset classes**: "
            f"{', '.join(persona.get('asset_classes', []))}",
            f"- **Preferred strategies**: {preferred}",
        ]
        if bias.get("selection_style"):
            sections.append(
                f"- **Selection style**: {bias['selection_style']}"
            )
        if risk_lines:
            sections.append(f"- **Risk rules**: {'; '.join(risk_lines)}")
        sections.append(f"- **Dashboard focus**: {focus}")
        if persona.get("prompt_guidance"):
            sections.append(
                f"- **Guidance**: {persona['prompt_guidance']}"
            )
        sections.append(
            f"\nTry: 'backtest {first_strategy} on RELIANCE'. This persona "
            "guides strategy choice and explanation style but does not "
            "bypass data, risk, approval, or OpenAlgo checks."
        )
        return "\n".join(sections)
    if tool_name == "list_sandbox_intents":
        intents = result.get("intents", [])
        if not intents:
            return (
                "No OpenAlgo sandbox or paper-trading intents are stored yet."
            )
        statuses = ", ".join(
            f"{item.get('intent_id')}={item.get('status')}"
            for item in intents[:8]
        )
        return (
            f"Found {len(intents)} sandbox/paper intent(s): {statuses}."
        )
    if tool_name == "prepare_sandbox_order_intent":
        return (
            f"Prepared sandbox order intent {result['intent_id']} for "
            f"{result['symbol']} {result['side']} {result['quantity']}. "
            f"Approval {result['approval_id']} is required before OpenAlgo "
            "submission."
        )
    if tool_name == "square_off_all":
        return (
            "Square-off sent — all open positions are being closed at "
            "market. Check the Account tab or your broker orderbook to confirm."
        )
    if tool_name == "cancel_all_orders":
        return (
            "Cancel-all sent — your pending orders are being cancelled. "
            "Check the Account tab to confirm."
        )
    if tool_name == "list_pending_approvals":
        approvals = result.get("approvals", [])
        if not approvals:
            return (
                "You have no orders waiting for approval. Tell me an order "
                "like 'buy 10 RELIANCE at market' to prepare one."
            )
        lines = [f"- {_pending_order_summary(a)}" for a in approvals[:10]]
        if len(approvals) == 1:
            return (
                "You have **1 order waiting for your approval:**\n"
                + "\n".join(lines)
                + "\n\nReply **approve** to send it to your broker."
            )
        return (
            f"You have **{len(approvals)} orders waiting for your "
            "approval:**\n" + "\n".join(lines)
            + "\n\nReply **approve** to send the most recent, or name the one "
            "you want."
        )
    if tool_name == "approve_pending_order":
        status = result.get("status")
        if status == "nothing_pending":
            return (
                "There are no orders waiting for approval right now."
            )
        if status == "not_found":
            return (
                "I couldn't find that pending order. "
                "Say 'show pending orders' to see what's waiting."
            )
        if status == "multiple_pending":
            lines = [
                f"- {_pending_order_summary(a)}"
                for a in result.get("approvals", [])[:10]
            ]
            return (
                "You have several orders waiting — which one?\n"
                + "\n".join(lines)
            )
        broker = result.get("broker_order_id")
        return (
            f"Approved and submitted. Order status: "
            f"**{result.get('order_status')}**"
            + (f" · broker order {broker}" if broker else "")
            + ". You can see it in the Account tab and in your broker's orderbook."
        )
    if tool_name == "prepare_direct_order":
        approval = result.get("approval_id")
        approval_line = (
            "Reply **approve** to send it to your broker."
            if approval
            else "It's approved and ready — reply **approve** to submit it."
        )
        return (
            f"**Order ready for your approval**\n"
            f"- {result['side']} {result['quantity']} {result['symbol']} "
            f"({result.get('exchange', 'NSE')})\n"
            f"- Type: {result.get('order_type', 'MARKET')} · "
            f"Product: {result.get('product', 'MIS')}\n\n"
            f"{approval_line} Nothing has been placed yet."
        )
    if tool_name == "import_openalgo_history":
        return (
            f"Imported {result['row_count']} verified OpenAlgo candle(s) for "
            f"{result['resolved_symbol']} {result['resolved_exchange']} into "
            f"dataset {result['dataset_id']}. You can now run a research or "
            "semi-auto backtest against this exact dataset."
        )
    if tool_name == "prepare_live_order_intent":
        return (
            f"⚠ **LIVE order ready for your approval**\n"
            f"- {result['side']} {result['quantity']} {result['symbol']} "
            f"({result.get('exchange', 'NSE')})\n\n"
            "This will place a **real** order with real money. Reply "
            "**approve** to send it to your broker, or ignore it to cancel. "
            "Nothing has been placed yet."
        )
    if tool_name == "assess_dataset_freshness":
        return (
            f"Dataset {result['dataset_id']} is {result['status']} for "
            f"{result['purpose']}: {result['reason']}."
        )
    if "intent_id" in result:
        return (
            f"Sandbox order intent {result['intent_id']} is "
            f"{result['status']}."
        )
    if tool_name == "run_backtest":
        summary = (
            f"Backtest {result['run_id']} completed for "
            f"{result['strategy']}. Net P&L: {result['net_pnl']:.2f}; "
            f"max drawdown: {result['max_drawdown']:.2f}; "
            f"return: {result['return_pct']:.4f}%."
        )
        if result.get("execution_mode") == "semi_auto":
            return (
                f"{summary} This is a historical simulation stored in "
                "Strategy Runs. Its risk decisions are available in Execution "
                "Controls to prepare a human-approved OpenAlgo analyzer order; "
                "only the submitted analyzer order will appear in OpenAlgo."
            )
        return summary
    if tool_name == "run_custom_strategy_spec":
        return (
            f"Custom strategy spec {result['custom_strategy_spec_id']} "
            f"backtest {result['run_id']} completed through the native "
            f"{result['strategy']} runtime. Net P&L: "
            f"{result['net_pnl']:.2f}; max drawdown: "
            f"{result['max_drawdown']:.2f}; return: "
            f"{result['return_pct']:.4f}%. No generated code was executed."
        )
    if tool_name == "get_custom_strategy_capabilities":
        contracts = result.get("data_contracts", {})
        rule_data = contracts.get("rule_backtesting", {})
        return (
            "Native custom-rule strategies support "
            f"{', '.join(result.get('supported_indicators', []))}; "
            f"position sides: {', '.join(result.get('supported_position_sides', []))}; "
            "and OHLCV backtests for "
            f"{', '.join(rule_data.get('supported_asset_classes', []))}. "
            "Numeric external data can be imported as governed point-in-time "
            "feature series and declared in feature_inputs before it is used "
            "in a rule."
        )
    if tool_name == "compile_custom_strategy_spec":
        return _compiled_strategy_response(result)
    if tool_name == "update_custom_strategy_spec":
        missing = result.get("missing_capabilities", [])
        state = (
            "executable by the native rule runtime"
            if not missing
            else "stored for review and is not executable yet"
        )
        return (
            f"Updated custom strategy {result['spec_id']} "
            f"(status: {result['status']}). The revised spec is {state}."
        )
    if tool_name == "create_custom_strategy_spec":
        missing = result.get("missing_capabilities", [])
        if missing:
            missing_values = ", ".join(
                str(item.get("value") or item.get("kind"))
                for item in missing
                if isinstance(item, dict)
            )
            guidance = _missing_capability_guidance(missing)
            return (
                f"Stored custom strategy draft {result['spec_id']} for review. "
                "It is not executable by the native rule runtime because it "
                f"requires: {missing_values or 'unsupported primitives'}. "
                f"Next governed data step: {guidance}"
            )
        return (
            f"Stored executable custom strategy draft {result['spec_id']}. "
            "It can be backtested through the native deterministic rule runtime; "
            "no generated code was executed."
        )
    if "run_id" in result:
        return f"Retrieved stored evidence for run {result['run_id']}."
    return "The requested tool completed successfully."


def _compiled_strategy_response(result: dict[str, Any]) -> str:
    spec = result.get("spec", {})
    risk = spec.get("risk", {}) or {}
    session = spec.get("session")
    lines = [
        "Here is the compiled strategy specification for your review. "
        "It has NOT been saved or executed.",
        f"- Instrument: {spec.get('symbol')} | timeframe: "
        f"{spec.get('timeframe')} | side: {spec.get('position_side')}",
        f"- Entry: {_describe_rules(spec.get('entry_rules'))}",
        f"- Exit: {_describe_rules(spec.get('exit_rules'))}",
    ]
    risk_parts = [
        f"{key.replace('_pct', '').replace('_', ' ')} "
        f"{value * 100:g}%" if key.endswith("_pct") else f"{key} {value}"
        for key, value in risk.items()
    ]
    if risk_parts:
        lines.append(f"- Risk: {', '.join(risk_parts)}")
    if session:
        lines.append(
            f"- Entries limited to {session.get('start')}-{session.get('end')}"
        )
    unparsed = result.get("unparsed_clauses") or []
    if unparsed:
        lines.append(
            "- I could not interpret: "
            + "; ".join(f"“{clause}”" for clause in unparsed)
        )
    for warning in result.get("warnings") or []:
        lines.append(f"- Note: {warning}")
    missing = result.get("missing_capabilities") or []
    if missing:
        reasons = "; ".join(
            str(item.get("reason") or item.get("value"))
            for item in missing
            if isinstance(item, dict)
        )
        lines.append(f"- Blocking issues before it can run: {reasons}")
    else:
        lines.append(
            "- Validation passed: this spec can run on the native "
            "deterministic rule runtime."
        )
    lines.append(
        "Review and edit it in the Strategies panel, then save it "
        "explicitly to create a governed draft."
    )
    return "\n".join(lines)


def _describe_rules(rules: Any) -> str:
    if not rules:
        return "none recognized"
    parts = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        prefix = (
            ""
            if index == 0
            else f"{str(rule.get('joiner', 'AND')).upper()} "
        )
        operator = str(rule.get("operator", "")).replace("_", " ")
        parts.append(
            f"{prefix}{rule.get('left')} {operator} {rule.get('right')}"
        )
    return "; ".join(parts) or "none recognized"


def _missing_capability_guidance(
    missing_capabilities: list[dict[str, Any]],
) -> str:
    values = " ".join(
        str(item.get("value", "")).lower()
        for item in missing_capabilities
        if isinstance(item, dict)
    )
    guidance: list[str] = []
    if any(token in values for token in ("iv", "skew", "oi", "open_interest")):
        guidance.append(
            "import a point-in-time numeric feature series through "
            "/datasets/features, then declare it in feature_inputs with an "
            "asof freshness limit"
        )
    if any(
        token in values
        for token in ("earnings", "fundamental", "edgar", "quandl")
    ):
        guidance.append(
            "import a point-in-time fundamentals feature series with source "
            "timestamps and revision metadata, then declare it in feature_inputs"
        )
    if any(token in values for token in ("news", "sentiment", "newsapi")):
        guidance.append(
            "import an archived news/sentiment numeric feature series with "
            "availability timestamps, then declare it in feature_inputs"
        )
    if not guidance:
        guidance.append(
            "import a governed numeric feature series with provenance and "
            "availability timestamps, then declare it in feature_inputs"
        )
    return "; ".join(guidance)


def grounded_multi_tool_response(
    results: list[tuple[str, dict[str, Any]]],
) -> str:
    """Compose a deterministic, evidence-backed answer for read-only checks."""
    summaries = [
        _grounded_fallback_response(tool_name, result)
        for tool_name, result in results
    ]
    return "Completed governed read-only checks:\n- " + "\n- ".join(summaries)
