// The agent platform: roster, supervisor, digest, leaderboard, arena and
// contests - the three tabs that were one until recently.
//
// It takes five things from core and hands back only what wires up the
// buttons. That is the point of the split: the coupling is countable now
// instead of ambient.

import { $, api, escapeHtml, showLogin, state, toast } from "./core.js";

const _agentPretty = (name) => name.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());

function renderAgents(agents) {
  const box = $("#agents-list");
  if (!box) return;
  if (!agents.length) {
    box.innerHTML = `<div class="empty-state">No agents registered yet.</div>`;
    return;
  }
  const rows = agents.map((a) => {
    const runnable = a.category !== "assistant";
    const action = runnable
      ? `<button class="secondary-button agent-run" data-agent-id="${escapeHtml(a.agent_id)}" data-agent-name="${escapeHtml(a.name)}">Run</button>`
      : `<span class="agent-chat-hint">Use the Chat tab</span>`;
    const last = a.last_run_at ? ` · last run ${escapeHtml(String(a.last_run_at).slice(0, 16).replace("T", " "))}` : "";
    return `<tr>
      <td><strong>${escapeHtml(_agentPretty(a.name))}</strong><br><small class="row-subname">v${escapeHtml(a.version)} · ${escapeHtml(a.category)}</small></td>
      <td>${escapeHtml(a.description)}<br><small class="row-subname">${a.run_count} run(s)${last}</small></td>
      <td>${action}</td>
    </tr>`;
  }).join("");
  box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Agent</th><th>What it does</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function loadAgents() {
  const box = $("#agents-list");
  if (!box) return;
  try {
    const payload = await api("/agents");
    renderAgents(payload.agents || []);
  } catch (error) {
    box.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

function renderLeaderboard(board) {
  const box = $("#leaderboard-list");
  if (!box) return;
  const ranked = board.ranked || [];
  const unranked = board.unranked || [];
  if (!ranked.length && !unranked.length) {
    box.innerHTML = `<div class="empty-state">No scores yet — run an agent above.</div>`;
    return;
  }
  const num = (v, digits = 3) => (v === null || v === undefined ? "—" : Number(v).toFixed(digits));
  const metricText = (m) => {
    if (m.out_of_sample_return_pct !== undefined) {
      // Out-of-sample only, and shown against the benchmark it had to beat.
      const parts = [`OOS ${num(m.out_of_sample_return_pct, 4)}%`];
      if (m.out_of_sample_excess_return_pct !== null && m.out_of_sample_excess_return_pct !== undefined) {
        const excess = Number(m.out_of_sample_excess_return_pct);
        parts.push(`${excess >= 0 ? "beat" : "trailed"} hold by ${num(Math.abs(excess), 4)}%`);
      }
      if (m.out_of_sample_sharpe !== null && m.out_of_sample_sharpe !== undefined) parts.push(`Sharpe ${num(m.out_of_sample_sharpe, 2)}`);
      if (m.out_of_sample_drawdown_pct !== null && m.out_of_sample_drawdown_pct !== undefined) parts.push(`DD ${num(m.out_of_sample_drawdown_pct, 3)}%`);
      if (m.windows && m.windows > 1) parts.push(`${m.windows_held_up}/${m.windows} windows`);
      parts.push(`${m.out_of_sample_trades} trades · ${m.verdict}`);
      return parts.join(" · ");
    }
    if (m.coverage !== undefined) {
      return `coverage ${Math.round(m.coverage * 100)}% · ${m.citations} citation(s)`;
    }
    if (m.precision !== undefined) {
      return `precision ${Math.round(m.precision * 100)}% · data ${Math.round(m.data_coverage * 100)}%`;
    }
    return "";
  };
  let html = "";
  if (ranked.length) {
    const rows = ranked.map((e) => `<tr>
        <td>${e.rank}</td>
        <td><strong>${escapeHtml(_agentPretty(e.name))}</strong><br><small class="row-subname">${escapeHtml(e.category)}</small></td>
        <td><strong>${escapeHtml(String(e.composite))}</strong></td>
        <td>${escapeHtml(metricText(e.metrics || {}))}</td>
        <td><small class="row-subname">run ${escapeHtml(e.run_id)}${e.eval_dataset_id ? `<br>${escapeHtml(e.eval_dataset_id)}` : ""}</small></td>
      </tr>`).join("");
    html += `<div class="table-wrap"><table><thead><tr><th>#</th><th>Agent</th><th>Score</th><th>Evidence</th><th>Traces to</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  if (unranked.length) {
    const items = unranked.map((e) => `<li><strong>${escapeHtml(_agentPretty(e.name))}</strong> — ${escapeHtml(e.reason || "inconclusive")}</li>`).join("");
    html += `<p class="leaderboard-unranked-head">Inconclusive (not ranked):</p><ul>${items}</ul>`;
  }
  box.innerHTML = html;
}

async function loadLeaderboard() {
  const box = $("#leaderboard-list");
  if (!box) return;
  try {
    renderLeaderboard(await api("/leaderboard"));
  } catch (error) {
    box.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

// --- Arena ---
async function loadArenaSeasons() {
  const select = $("#arena-season-select");
  if (!select) return;
  try {
    const { seasons } = await api("/arena/seasons");
    if (!seasons.length) {
      select.innerHTML = `<option value="">No seasons yet</option>`;
      $("#arena-standings").innerHTML = `<div class="empty-state">No season running. Seasons are created via the API or a scheduled job.</div>`;
      return;
    }
    const current = select.value;
    select.innerHTML = seasons.map((s) =>
      `<option value="${escapeHtml(s.season_id)}">${escapeHtml(s.name)} · ${escapeHtml(s.symbol)} · ${s.entries} entrant(s)</option>`
    ).join("");
    if (current && seasons.some((s) => s.season_id === current)) select.value = current;
    await loadArenaStandings();
  } catch (error) {
    select.innerHTML = `<option value="">${escapeHtml(error.message)}</option>`;
  }
}

async function loadArenaStandings() {
  const box = $("#arena-standings");
  const seasonId = $("#arena-season-select")?.value;
  if (!box || !seasonId) return;
  try {
    const board = await api(`/arena/seasons/${encodeURIComponent(seasonId)}/standings`);
    const ranked = board.standings || [];
    const missing = board.unavailable || [];
    let html = "";
    if (ranked.length) {
      const rows = ranked.map((s) => `<tr>
          <td>${s.rank}</td>
          <td><strong>${escapeHtml(_agentPretty(s.agent_id.split("@")[0]))}</strong><br><small class="row-subname">${escapeHtml(s.strategy_name)}</small></td>
          <td class="${s.return_pct >= 0 ? "pnl-up" : "pnl-down"}">${escapeHtml(String(s.return_pct))}%</td>
          <td>₹${Number(s.equity).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</td>
          <td>${s.trades ?? "—"}</td>
          <td><small class="row-subname">${escapeHtml(String(s.as_of))}</small></td>
        </tr>`).join("");
      html += `<div class="table-wrap"><table><thead><tr><th>#</th><th>Agent</th><th>Return</th><th>Equity</th><th>Trades</th><th>As of</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    } else {
      html += `<div class="empty-state">No standings yet — advance a day to run the season.</div>`;
    }
    if (missing.length) {
      html += `<p class="leaderboard-unranked-head">Data missing (not scored): ${missing.map((m) => escapeHtml(m.agent_id)).join(", ")}</p>`;
    }
    box.innerHTML = html;
  } catch (error) {
    box.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function tickArena() {
  const seasonId = $("#arena-season-select")?.value;
  const button = $("#arena-tick");
  if (!seasonId) { toast("No season selected"); return; }
  if (button) button.disabled = true;
  try {
    const result = await api(`/arena/seasons/${encodeURIComponent(seasonId)}/tick`, { method: "POST" });
    const missing = (result.entries || []).filter((e) => e.data_status !== "ok").length;
    toast(missing ? `Day advanced; ${missing} entry(ies) had no data` : "Day advanced");
    await loadArenaStandings();
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

// --- Data coverage ---

async function loadSupervisorFindings() {
  const box = $("#supervisor-findings");
  if (!box) return;
  try {
    const { findings } = await api("/supervisor/findings");
    if (!findings.length) {
      box.innerHTML = `<div class="empty-state">Nothing flagged. The supervisor reports only material changes — silence means the agents are steady.</div>`;
      return;
    }
    box.innerHTML = findings.map((f) => `
      <div class="supervisor-finding ${escapeHtml(f.severity)}">
        <div>
          <strong>${escapeHtml(f.summary)}</strong>
          <br><small class="row-subname">${escapeHtml(f.kind.replaceAll("_", " "))} · ${escapeHtml(String(f.detected_at || "").slice(0, 16).replace("T", " "))}${f.detail?.run_id ? " · run " + escapeHtml(f.detail.run_id) : ""}</small>
        </div>
        <button class="secondary-button finding-ack" data-finding-id="${escapeHtml(f.finding_id)}">Acknowledge</button>
      </div>`).join("");
  } catch (error) {
    box.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function runSupervisorSweep() {
  const button = $("#supervisor-sweep");
  if (button) button.disabled = true;
  try {
    const result = await api("/supervisor/sweep", { method: "POST", body: JSON.stringify({}) });
    const n = (result.findings || []).length;
    toast(n ? `Sweep done — ${n} finding(s)` : `Sweep done — ${(result.ran || []).length} agent(s) re-run, nothing flagged`);
    await loadSupervisorFindings();
    await loadLeaderboard();
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

async function acknowledgeFinding(findingId) {
  try {
    await api(`/supervisor/findings/${encodeURIComponent(findingId)}/acknowledge`, { method: "POST", body: JSON.stringify({}) });
    await loadSupervisorFindings();
  } catch (error) {
    toast(error.message);
  }
}

// --- Daily digest (what changed, what's stale, what degraded) ---
function renderDigestSection(section) {
  const items = (section.items || []).map((item) => `
    <li>${escapeHtml(item.text)}${item.attribution ? ` <small class="row-subname">— ${escapeHtml(item.attribution)}</small>` : ""}</li>`).join("");
  const leaders = (section.leaderboard_top || []).map((entry) => `
    <li>#${escapeHtml(String(entry.rank))} ${escapeHtml(entry.name)} · ${escapeHtml(String(entry.composite))} <small class="row-subname">— ${escapeHtml(entry.attribution || "")}</small></li>`).join("");
  const coverage = section.coverage && section.coverage.price_coverage_pct !== undefined
    ? `<p class="row-subname">Price coverage ${escapeHtml(String(section.coverage.price_coverage_pct))}% · fundamentals ${escapeHtml(String(section.coverage.fundamentals_coverage_pct))}%</p>`
    : "";
  // A section with nothing in it says so; it never renders as blank, because
  // blank reads as "not checked" and that is a different claim.
  const body = items || leaders
    ? `<ul>${items}${leaders}</ul>`
    : `<p class="row-subname">Nothing to report.</p>`;
  const gaps = (section.gaps || []).map((gap) => `<li>${escapeHtml(gap)}</li>`).join("");
  return `
    <div class="digest-section">
      <h3>${escapeHtml(section.title || section.section)}</h3>
      <small class="row-subname">${escapeHtml(section.source || "")}</small>
      ${body}
      ${coverage}
      ${gaps ? `<div class="empty-state"><strong>Gaps</strong><ul>${gaps}</ul></div>` : ""}
    </div>`;
}

function renderDigest(digest) {
  const box = $("#digest-body");
  if (!box) return;
  if (!digest || !digest.digest_id) {
    box.innerHTML = `<div class="empty-state">No digest yet. Generate one to see what has changed since the agents last ran.</div>`;
    return;
  }
  box.innerHTML = `
    <p><strong>${escapeHtml(digest.headline || "")}</strong>
      <br><small class="row-subname">${escapeHtml(String(digest.generated_at || "").slice(0, 16).replace("T", " "))}</small></p>
    ${(digest.sections || []).map(renderDigestSection).join("")}`;
}

async function loadDigest() {
  const box = $("#digest-body");
  if (!box) return;
  try {
    renderDigest(await api("/supervisor/digest"));
  } catch (error) {
    box.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function generateDigest() {
  const button = $("#digest-generate");
  if (button) button.disabled = true;
  try {
    const symbol = ($("#agent-symbol")?.value || "").trim();
    const digest = await api("/supervisor/digest", {
      method: "POST",
      body: JSON.stringify(symbol ? { symbol } : {}),
    });
    renderDigest(digest);
    toast(digest.headline || "Digest generated");
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

// --- Contests ---
async function loadContests() {
  const box = $("#contests-list");
  if (!box) return;
  try {
    const { contests } = await api("/contests");
    if (!contests.length) {
      box.innerHTML = `<div class="empty-state">No contests yet. Create one via <code>POST /contests</code> or the SDK.</div>`;
      return;
    }
    const rows = contests.map((c) => `<tr>
        <td><strong>${escapeHtml(c.name)}</strong><br><small class="row-subname">${escapeHtml(c.symbol)}</small></td>
        <td><span class="watch-status watch-${c.status === "open" ? "active" : "triggered"}">${escapeHtml(c.status)}</span></td>
        <td>${escapeHtml(String(c.closes_at || "").slice(0, 16).replace("T", " "))}</td>
        <td><small class="row-subname">${c.dataset_hash ? escapeHtml(c.dataset_hash.slice(0, 12)) + "…" : "no data frozen"}</small></td>
        <td><button class="secondary-button contest-results" data-contest-id="${escapeHtml(c.contest_id)}">Results</button></td>
      </tr>`).join("");
    box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Contest</th><th>Status</th><th>Closes</th><th>Dataset hash</th><th></th></tr></thead><tbody>${rows}</tbody></table></div><div id="contest-results-detail"></div>`;
  } catch (error) {
    box.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}


async function streamSse(path, onEvent) {
  const authHeaders = state.token ? { Authorization: `Bearer ${state.token}` } : {};
  const response = await fetch(path, { headers: authHeaders });
  if (!response.ok) {
    if (response.status === 401) showLogin();
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed (${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split;
    while ((split = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      // ":" frames are keep-alive comments — nothing to show.
      if (!frame.trim() || frame.startsWith(":")) continue;
      const lines = frame.split("\n");
      const name = (lines.find((l) => l.startsWith("event: ")) || "").slice(7);
      const data = lines
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("\n");
      onEvent(name || "message", data ? JSON.parse(data) : {});
    }
  }
}

function renderAgentProgress(agentName, steps, finished) {
  const items = steps
    .map((s) => `<li>${escapeHtml(s.message || s.step || "")}</li>`)
    .join("");
  return `
    <div class="agent-result">
      <p><strong>${escapeHtml(_agentPretty(agentName))}</strong> ${finished ? "finished" : "is working…"}</p>
      ${items ? `<ol class="agent-steps">${items}</ol>` : ""}
    </div>`;
}

function renderAgentResult(agentName, payload) {
  const gaps = (payload.gaps || []).map((g) => `<li>${escapeHtml(g)}</li>`).join("");
  return `
    <div class="agent-result">
      <p><strong>${escapeHtml(_agentPretty(agentName))}</strong> finished:
        <span class="watch-status watch-${payload.status === "ok" ? "active" : "triggered"}">${escapeHtml(payload.status)}</span>
        · ${(payload.evidence || []).length} evidence item(s)
        · took ${escapeHtml(String(payload.cost?.seconds ?? "?"))}s
        · recorded as ${escapeHtml(payload.run_id)}</p>
      ${gaps ? `<p>Honest gaps:</p><ul>${gaps}</ul>` : ""}
      <details><summary>Full findings</summary><pre>${escapeHtml(JSON.stringify(payload.findings, null, 2))}</pre></details>
    </div>`;
}

async function runAgent(agentId, agentName, button) {
  const result = $("#agent-run-result");
  const symbol = ($("#agent-symbol")?.value || "RELIANCE").trim().toUpperCase();
  const symbol2 = ($("#agent-symbol2")?.value || "").trim().toUpperCase();
  if (button) button.disabled = true;
  result.innerHTML = `<div class="empty-state">Starting ${escapeHtml(_agentPretty(agentName))}…</div>`;
  try {
    // The comparator takes a list of symbols, which only the POST route
    // accepts; everything else streams its progress while it runs.
    if (agentName === "comparator") {
      const payload = await api(`/agents/${encodeURIComponent(agentId)}/run`, {
        method: "POST",
        body: JSON.stringify({ symbols: [symbol, symbol2].filter(Boolean) }),
      });
      result.innerHTML = renderAgentResult(agentName, payload);
    } else {
      const steps = [];
      await streamSse(
        `/agents/${encodeURIComponent(agentId)}/run/stream?symbol=${encodeURIComponent(symbol)}`,
        (name, data) => {
          if (name === "progress") {
            steps.push(data);
            result.innerHTML = renderAgentProgress(agentName, steps, false);
          } else if (name === "result") {
            result.innerHTML = renderAgentResult(agentName, data);
          } else if (name === "failed") {
            result.innerHTML = `<div class="empty-state">${escapeHtml(data.error || "The run failed.")}</div>`;
          }
        },
      );
    }
    await loadAgents();
    await loadLeaderboard();
  } catch (error) {
    result.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  } finally {
    if (button) button.disabled = false;
  }
}


// Top-of-landing-page live view: open positions and working orders, so the
// client can see and act on live trades without leaving the chat.

export {
  acknowledgeFinding,
  generateDigest,
  loadAgents,
  loadArenaSeasons,
  loadArenaStandings,
  loadContests,
  loadDigest,
  loadLeaderboard,
  loadSupervisorFindings,
  runAgent,
  runSupervisorSweep,
  tickArena,
};
