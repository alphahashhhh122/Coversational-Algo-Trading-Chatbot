# Demo script — Agentic Trading Lab

A 10-minute walkthrough. Each step names the *idea* being demonstrated, because
the interesting part is the design, not the clicking.

**Setup:** `python -m uvicorn iimc_trading_platform.asgi:app` (omit `--reload`
for a stable demo), then open `http://127.0.0.1:8000`.

---

## 1. It's a conversation, not a form  ·  Chat tab

> "give me a full rundown on Reliance"

A plain-language question routes to a multi-analyst research agent — valuation,
fundamentals, technicals and news gathered in parallel — and returns a briefing
built only from real data.

**Point out:** you never typed a ticker field, a date range, or an indicator
name. Routing is deterministic-first (fast, predictable) and falls back to an
LLM for phrasings the rules don't cover.

## 2. It refuses to fake things  ·  Chat tab

> "what's the price of Reliance right now"

If the broker token has expired, the answer says so plainly instead of showing
a stale or invented price.

**Point out:** this is the load-bearing property of the whole platform. A
system that quietly invents a number is worse than one that admits a gap,
because you can't tell which answers to trust.

## 3. Capabilities are agents  ·  Agents tab

Seven registered agents — research, strategy, monitor, and the chat assistant
itself. Hit **Run** on *Strategy Validator*.

**Point out:** the result carries findings, **evidence** (dataset id, the exact
train/test split), and an honest list of gaps. Every run is recorded, which is
what makes the next step possible.

## 4. The leaderboard is evidence, not opinion  ·  Agents tab

Scroll to **Leaderboard**. Each row shows the score, the metrics behind it, and
the run id it traces to.

**Point out — the key design decision:** strategy agents are ranked on
**out-of-sample results only**. A configuration that looked brilliant on the
data it was fitted to and fell apart on unseen data is *penalised*, not
celebrated. Agents without enough evidence appear as **inconclusive** rather
than ranked at zero — because "we don't know yet" and "it scored zero" are
different claims.

## 5. Agents compete on real markets  ·  Agents tab → Arena

Pick a season and press **Advance day**.

**Point out:** they're trading a simulated ledger fed by real market data.
There is **no broker code path in the arena at all** — not even a sandbox one —
and a test enforces that against the parsed source. That's exactly what lets
agents act autonomously without weakening the rule that a human approves every
real order. Days without data are marked *missing*, never filled in.

## 6. You can author an agent without code  ·  Chat → Strategies

> "Create a Reliance 5 minute strategy that buys when EMA 9 crosses above
> EMA 21 and exits when it crosses below, with a 2 percent stop loss"

Review the compiled spec, save it, register it — it now competes on the same
leaderboard.

**Point out:** your English never became Python. It compiled to a **rule spec**
— data that a deterministic runtime interprets. No `eval`, no generated source.
That's the standard defence against prompt-injection-to-code-execution.

## 7. Disagreement is a finding  ·  `POST /committee`

```bash
curl -s -X POST http://127.0.0.1:8000/committee \
  -H "Content-Type: application/json" \
  -d '{"symbol":"RELIANCE"}' | python -m json.tool
```

**Point out:** when members disagree, the brief reports both positions with
attribution instead of averaging them. An averaged number would hide the single
most useful signal a multi-agent system produces — that the evidence is mixed.

## 8. Anything the UI does, code can do  ·  SDK

```python
from iimc_trading_platform.sdk import ATLClient

atl = ATLClient("http://127.0.0.1:8000")
print([a["name"] for a in atl.list_agents()])
print(atl.run_agent("market_researcher", symbol="RELIANCE")["status"])
for row in atl.leaderboard()["ranked"]:
    print(row["rank"], row["name"], row["composite"], "->", row["run_id"])
```

**Point out:** dependency-free (stdlib only), and it has **no approve/order
method** — the API it wraps doesn't expose one.

## 9. Same platform from any AI client  ·  MCP

With the server registered in Claude Desktop (see `docs/YOUR_TASKS.md`), ask it
to list agents or run the researcher.

**Point out:** MCP is JSON-RPC over stdin/stdout. The exposed subset is
researcher-level; a test asserts nothing callable there contains
`approve`/`order`/`submit`/`execute`/`trade`.

---

## Closing line

> Every number on this platform traces to a stored run on a named dataset.
> Nothing is invented, losing strategies are reported as losing, and no agent
> — registered, scheduled, authored, or remote — can reach the broker. A human
> approves every real order.
