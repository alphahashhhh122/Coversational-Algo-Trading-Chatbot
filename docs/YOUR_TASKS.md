# Things only you can do (and what they teach)

Everything else in the build is automatable. These need your credentials, your
machine, or your judgement — so they're yours. Each one has a *why* attached,
because the point is to understand the system, not just run commands.

---

## 1. Connect the platform to Claude Desktop over MCP  ⏱ ~5 minutes

**What it does:** lets you talk to your own trading platform from Claude
Desktop — "list my agents", "run the deep researcher on TCS", "show the
leaderboard" — using the same governed tools the web app uses.

**Do this:** add the server to Claude Desktop's config file.

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "iimc-trading": {
      "command": "python",
      "args": ["-m", "iimc_trading_platform.mcp_server"],
      "cwd": "C:/Users/nirwa/Documents/Codex/2026-07-01/can-u/openalgo_project"
    }
  }
}
```

Restart Claude Desktop, then ask it to list your agents.

**What you're learning — how MCP actually works.** MCP is just **JSON-RPC 2.0
over stdin/stdout**. There is no network, no port, no auth handshake. The
client launches your process, writes a JSON line, and reads a JSON line back.
Three methods carry almost everything:

| Method | Meaning |
| --- | --- |
| `initialize` | version + capability handshake |
| `tools/list` | "what can you do?" → tool names + JSON Schemas |
| `tools/call` | "do this one" → result or `isError` |

You can watch it yourself — this is exactly what Claude Desktop does:

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"me","version":"1"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | python -m iimc_trading_platform.mcp_server
```

**The safety design worth noticing:** the server exposes the *researcher*
subset of tools (73 of them, including `list_agents`, `run_agent`,
`get_leaderboard`). Order approval and live submission are **not** in that
subset and never will be — a test asserts no callable on the MCP surface
contains `approve`, `order`, `submit`, `execute`, or `trade`. So connecting
Claude Desktop can never place a trade, no matter what it's asked.

---

## 2. Refresh the Dhan data token  ⏱ ~3 minutes

**Why it's blocking:** OpenAlgo's own logs show `Dhan API Error 808 / DH-901:
"Client ID or user generated access token is invalid or expired"`. Everything
depending on live data (quotes, RSI watches, arena days) honestly reports
unavailable until this is refreshed. The platform is behaving correctly; the
token is stale.

**Do this:**
1. In Dhan (DhanHQ → My Profile → **Access Token / Data APIs**), generate a
   fresh token — including the **Data API** token if you use market data.
2. Update `BROKER_API_SECRET` (and `BROKER_API_SECRET_MARKET`) in
   `~/openalgo/.env`.
3. Restart OpenAlgo. Its dashboard balance goes non-zero when it worked.

**What you're learning:** brokers issue *short-lived* tokens deliberately, and
there are usually **two** — one for trading, one for market data. Our error was
on the data endpoint, which is why quotes failed while the session still looked
"connected". A platform that faked a price here would be worse than useless;
ours marks the day `data_missing` instead.

---

## 3. Design decisions only you can make

These shape the product. I've flagged them with a recommendation, but the call
is yours.

**a) Which agents matter to you?** The roster is seven right now. What would
*you* want competing — a mean-reversion agent? A sector-rotation researcher? An
earnings-drift specialist? Tell me the idea in plain English and it becomes a
registered, ranked agent (see §4 — you can now author these yourself).

**b) Arena cadence.** End-of-day ticks (recommended: robust, survives token
gaps) or intraday 5-minute (flashier, more fragile). Currently EOD.

**c) Contest visibility.** Private for the professor demo, or a public
invitational on the repo?

**d) LLM-as-judge for research scoring.** Currently **off** on purpose —
an LLM scoring LLM output tends to reward fluency over correctness. Coverage
and citation counts are dumber but honest. Worth revisiting only with
human-calibrated spot checks.

---

## 4. Author a strategy — no code  ⏱ ~2 minutes

Describe it in the chat, e.g.:

> "Create a Reliance 5 minute strategy that buys when EMA 9 crosses above
> EMA 21 and exits when it crosses below, with a 2 percent stop loss"

Review the compiled spec in the **Strategies** tab, save it, and register it as
an agent. It then appears in the gallery and is ranked on the same leaderboard
as the built-ins.

**What you're learning — why this is safe.** Your English never becomes Python.
It compiles to a **rule spec**: plain data (indicators, comparisons,
thresholds) that a deterministic runtime interprets. There is no `eval`, no
generated source file, no import of user content. This is the standard defence
against prompt-injection-to-code-execution: *interpret data, never execute
generated code*. The spec is also validated against the runtime's declared
capabilities before it can run at all — an unsupported indicator is refused
rather than half-working.

And registration is **append-only**: editing your strategy creates `v2` rather
than mutating `v1`, so a leaderboard entry can never silently change meaning
and old runs still point at the exact spec that produced them.
