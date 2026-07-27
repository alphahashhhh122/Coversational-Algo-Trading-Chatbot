const state = {
  sessionId: localStorage.getItem("iimc_chat_session")
    || `session_ui_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`,
  health: null,
  datasets: [],
  runs: [],
  strategies: [],
  experiments: [],
  operations: null,
  jobs: [],
  tasks: [],
  backups: [],
  evaluations: [],
  retrievalEvaluations: [],
  retention: null,
  alerts: [],
  openalgoMonitor: null,
  marketNews: null,
  personas: [],
  knowledgeDocuments: [],
  customStrategySpecs: [],
  nlCompiledResult: null,
  platformSummary: null,
  dashboardWidgets: JSON.parse(
    localStorage.getItem("iimc_dashboard_widgets")
    || '["research","assets","backtests","openalgo","news"]',
  ),
  autoRefresh: localStorage.getItem("iimc_auto_refresh") === "true",
  autoRefreshTimer: null,
  selectedRuns: new Set(),
  token: sessionStorage.getItem("iimc_access_token"),
  principal: null,
};

localStorage.setItem("iimc_chat_session", state.sessionId);

const $ = (selector) => document.querySelector(selector);

function applyTheme(dark) {
  document.documentElement.classList.toggle("dark-theme", dark);
  const button = $("#theme-toggle");
  if (button) button.textContent = dark ? "Light mode" : "Dark mode";
}

applyTheme(
  localStorage.getItem("iimc_theme")
    ? localStorage.getItem("iimc_theme") === "dark"
    : window.matchMedia("(prefers-color-scheme: dark)").matches,
);
const DEFAULT_DASHBOARD_WIDGETS = ["research", "assets", "backtests", "openalgo", "risk", "execution"];

function marketDatasets() {
  return state.datasets.filter((dataset) => (
    dataset.storage_table === "market_ohlcv"
    || dataset.storage_table === "options_ohlcv"
  ));
}

async function api(path, options = {}) {
  const authHeaders = state.token
    ? { Authorization: `Bearer ${state.token}` }
    : {};
  let response;
  try {
    response = await fetch(path, {
      headers: {
        "Content-Type": "application/json",
        ...authHeaders,
        ...(options.headers || {}),
      },
      ...options,
    });
  } catch (networkError) {
    $("#api-dot").className = "status-dot offline";
    $("#api-label").textContent = "API offline";
    throw new Error("Cannot reach the server. Check that the platform is running.");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login") {
      showLogin();
    }
    const detail = payload.detail;
    if (path === "/chat" && typeof detail === "object") {
      throw new Error(
        "I could not complete that chat request right now. Try a quote, "
        + "market update, funds, positions, orders, trades, backtest, or "
        + "OpenAlgo status while the model connection recovers.",
      );
    }
    const message = typeof detail === "string"
      ? detail
      : [detail?.message, detail?.cause].filter(Boolean).join(" Cause: ")
        || payload.message
        || `Request failed with status ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: digits,
  }).format(value);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function summarizeTimelineEvent(event) {
  const details = event.details || {};
  if (event.event_type === "signal") {
    return `${details.signal_type} ${details.direction}: ${details.reason}`;
  }
  if (event.event_type === "risk_decision") {
    const outcome = details.approved ? "approved" : "rejected";
    return `${outcome}: ${details.reason} (${details.requested_quantity} requested, ${details.approved_quantity} approved)`;
  }
  if (event.event_type === "order") {
    return `${details.side} ${details.quantity} ${details.symbol} at ${formatNumber(details.price)} · ${details.status}`;
  }
  if (event.event_type === "fill") {
    return `${details.side} ${details.quantity} at ${formatNumber(details.price)} · P&L ${formatNumber(details.realized_pnl)}`;
  }
  return event.event_type;
}

function renderTimelineRows(events) {
  return events.map((event) => `
    <tr>
      <td><span class="event-type ${escapeHtml(event.event_type)}">${escapeHtml(event.event_type.replaceAll("_", " "))}</span></td>
      <td>${escapeHtml(event.timestamp)}</td>
      <td>
        <strong class="event-id">${escapeHtml(event.entity_id)}</strong>
        <small>${event.parent_id ? `Parent ${escapeHtml(event.parent_id)}` : "Root event"}</small>
      </td>
      <td class="event-summary">
        ${escapeHtml(summarizeTimelineEvent(event))}
        <details>
          <summary>Details</summary>
          <pre>${escapeHtml(JSON.stringify(event.details, null, 2))}</pre>
        </details>
      </td>
    </tr>
  `).join("");
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  window.setTimeout(() => element.classList.remove("show"), 2800);
}

async function persistDashboardPreferences() {
  try {
    await api("/platform/dashboard/preferences", {
      method: "PUT",
      body: JSON.stringify({
        widgets: state.dashboardWidgets,
        auto_refresh: state.autoRefresh,
      }),
    });
  } catch (error) {
    toast(`Dashboard preference saved locally only: ${error.message}`);
  }
}

function applyDashboardPreferences(preferences) {
  if (!preferences) return;
  state.dashboardWidgets = preferences.widgets || DEFAULT_DASHBOARD_WIDGETS;
  state.autoRefresh = Boolean(preferences.auto_refresh);
  localStorage.setItem(
    "iimc_dashboard_widgets",
    JSON.stringify(state.dashboardWidgets),
  );
  localStorage.setItem("iimc_auto_refresh", String(state.autoRefresh));
}

function setAutoRefresh(enabled, persist = true) {
  state.autoRefresh = enabled;
  localStorage.setItem("iimc_auto_refresh", String(enabled));
  const button = $("#toggle-live-dashboard");
  if (button) {
    button.textContent = enabled ? "Auto refresh on" : "Auto refresh off";
    button.classList.toggle("active-refresh", enabled);
  }
  if (state.autoRefreshTimer) {
    window.clearInterval(state.autoRefreshTimer);
    state.autoRefreshTimer = null;
  }
  if (enabled) {
    state.autoRefreshTimer = window.setInterval(async () => {
      try {
        state.operations = null;
        await loadOverview();
      } catch (error) {
        toast(`Auto refresh paused: ${error.message}`);
        setAutoRefresh(false);
      }
    }, 15000);
  }
  if (persist) {
    persistDashboardPreferences();
  }
}

function setView(view) {
  const labels = {
    workspace: ["Chat", "Ask anything in plain language."],
    runs: ["Backtests", "Test strategies on historical data."],
    strategies: ["Strategies", "Create and save strategies in plain English."],
    data: ["Data", "Market data, documents, and financials."],
    monitor: ["Account", "Your live broker account: funds, positions, orders, trades."],
    agents: ["Agents", "Registered agents you can run — every run is recorded with evidence."],
    settings: ["Settings", "Configuration and overview."],
  };
  document.querySelectorAll(".view").forEach((node) => node.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((node) => node.classList.remove("active"));
  $(`#view-${view}`).classList.add("active");
  document.querySelectorAll(`.nav-item[data-view="${view}"]`).forEach((node) => {
    node.classList.add("active");
  });
  $("#view-title").textContent = labels[view][0];
  $("#view-subtitle").textContent = labels[view][1];
}

async function loadHealth() {
  try {
    state.health = await api("/health");
    const checks = state.health.checks;
    $("#api-dot").className = "status-dot online";
    $("#api-label").textContent = "API healthy";
    $("#database-status").textContent = checks.core_schema_complete ? "Healthy" : "Attention";
    const llmProvider = checks.llm_provider || "openai";
    const llmConfigured = llmProvider === "groq"
      ? checks.groq_api_key_configured
      : checks.openai_api_key_configured;
    $("#llm-status").textContent = llmConfigured
      ? `${llmProvider.toUpperCase()} configured`
      : `${llmProvider.toUpperCase()} key required`;
    $("#openalgo-status").textContent = checks.openalgo_api_key_configured ? "Configured" : "Not configured";
    const liveBadge = $("#live-badge");
    if (liveBadge) {
      liveBadge.textContent = checks.live_trading_disabled
        ? "Live disabled"
        : "LIVE ENABLED";
      liveBadge.className = checks.live_trading_disabled
        ? "badge safe"
        : "badge live";
    }
    const modeBanner = $("#execution-mode-banner");
    if (modeBanner) {
      if (checks.live_trading_disabled) {
        modeBanner.textContent = "PAPER MODE — live trading is disabled by configuration. Orders route to the OpenAlgo analyzer only.";
        modeBanner.className = "mode-banner paper";
      } else {
        modeBanner.textContent = "⚠ LIVE TRADING ENABLED — approved live intents submit REAL broker orders. Every live order still requires a live risk decision and explicit human approval.";
        modeBanner.className = "mode-banner live";
      }
    }
    if (checks.authentication_required) {
      if (state.token) await loadPrincipal();
      else showLogin();
    } else {
      state.principal = {
        username: "local_development",
        role: "admin",
        authenticated: false,
      };
      renderPrincipal();
    }
  } catch (error) {
    $("#api-dot").className = "status-dot offline";
    $("#api-label").textContent = "API unavailable";
    toast(error.message);
  }
}

async function loadPrincipal() {
  try {
    state.principal = await api("/auth/me");
    hideLogin();
    renderPrincipal();
  } catch (error) {
    state.token = null;
    sessionStorage.removeItem("iimc_access_token");
    showLogin();
  }
}

function renderPrincipal() {
  if (!state.principal) return;
  $("#user-badge").textContent = `${state.principal.username} · ${state.principal.role}`;
  $("#logout-button").classList.toggle(
    "hidden",
    !state.principal.authenticated,
  );
  $("#experiment-form").classList.toggle(
    "hidden",
    !hasRole("researcher"),
  );
}

function showLogin() {
  $("#auth-overlay").classList.remove("hidden");
}

function hideLogin() {
  $("#auth-overlay").classList.add("hidden");
  $("#auth-error").textContent = "";
}

async function submitLogin(event) {
  event.preventDefault();
  $("#auth-error").textContent = "";
  const button = event.target.querySelector("button[type=submit]");
  if (button) button.disabled = true;
  try {
    const payload = await api("/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("#login-username").value,
        password: $("#login-password").value,
      }),
    });
    state.token = payload.access_token;
    sessionStorage.setItem("iimc_access_token", state.token);
    state.principal = payload.user;
    renderPrincipal();
    hideLogin();
    await restoreChatHistory();
    await loadOverview();
  } catch (error) {
    $("#auth-error").textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
}

async function logout() {
  try {
    await api("/auth/logout", { method: "POST" });
  } catch (error) {
    // Clear local state even if the server session has already expired.
  }
  state.token = null;
  state.principal = null;
  sessionStorage.removeItem("iimc_access_token");
  showLogin();
}

async function loadOverview() {
  const [datasets, runs, strategies, customSpecs, experiments, documents, platform, marketNews, personas, preferences] = await Promise.all([
    api("/datasets"),
    api("/runs?limit=50"),
    api("/strategies"),
    api("/custom-strategy-specs"),
    api("/experiments/robustness?limit=50"),
    api("/knowledge/documents"),
    api("/platform/summary"),
    api("/market-news/latest?limit=5"),
    api("/personas"),
    api("/platform/dashboard/preferences").catch(() => null),
  ]);
  applyDashboardPreferences(preferences);
  setAutoRefresh(state.autoRefresh, false);
  state.datasets = datasets.datasets || [];
  state.runs = runs.runs || [];
  state.strategies = strategies.strategies || [];
  state.customStrategySpecs = customSpecs.custom_strategy_specs || [];
  state.experiments = experiments.experiments || [];
  state.platformSummary = platform;
  state.marketNews = marketNews;
  state.personas = personas.personas || [];
  const rowCount = state.datasets.reduce((sum, item) => sum + Number(item.row_count || 0), 0);
  $("#metric-rows").textContent = formatNumber(rowCount, 0);
  $("#metric-quality").textContent = state.datasets[0]?.quality?.status || "No dataset loaded";
  $("#metric-runs").textContent = formatNumber(state.runs.length, 0);
  $("#metric-documents").textContent = formatNumber(documents.documents?.length || 0, 0);
  state.knowledgeDocuments = documents.documents || [];
  renderKnowledgeDocuments();
  renderCommandCenter(documents.documents?.length || 0);
  renderPersonas();
  renderMarketNewsPanel();
  renderRuns();
  renderDatasets();
  renderExperiments();
  // Heavy secondary panels load in the background so the workspace is
  // interactive immediately.
  loadAccount().catch(() => {});
  loadLiveTrades().catch(() => {});
  loadSettings();
}

function renderMarketNewsPanel(message = "") {
  const news = state.marketNews || {};
  const configured = Boolean(news.news_configured);
  const articles = news.articles || [];
  const statusText = message || (
    configured
      ? `${formatNumber(articles.length, 0)} headline(s)`
      : "News provider not configured yet."
  );
  const status = $("#market-news-status");
  if (status) {
    status.textContent = statusText;
    status.className = `market-news-status ${configured ? "ready" : "attention"}`;
  }
  const list = $("#market-news-list");
  if (list) {
    list.innerHTML = articles.length
      ? articles.map((article) => `
        <article class="market-news-item">
          <strong>${escapeHtml(article.title)}</strong>
          <span>${escapeHtml(article.source || "unknown")} · ${escapeHtml(article.published_at || article.retrieved_at || "stored")}</span>
        </article>
      `).join("")
      : `<div class="empty-state">No headlines yet.</div>`;
  }
  renderLandingNews();
}

// Compact read-only headlines shown on the Chat landing page.
function renderLandingNews() {
  const list = $("#landing-news-list");
  const status = $("#landing-news-status");
  if (!list) return;
  const news = state.marketNews || {};
  const configured = Boolean(news.news_configured);
  const articles = (news.articles || []).slice(0, 6);
  if (status) {
    status.textContent = configured
      ? "Latest market headlines"
      : "Connect a news provider to see headlines.";
  }
  list.innerHTML = articles.length
    ? articles.map((article) => {
      const meta = `${article.source || "unknown"} · ${article.published_at || article.retrieved_at || "stored"}`;
      const title = escapeHtml(article.title);
      return article.url
        ? `<a class="market-news-item" href="${escapeHtml(article.url)}" target="_blank" rel="noopener noreferrer"><strong>${title}</strong><span>${escapeHtml(meta)}</span></a>`
        : `<article class="market-news-item"><strong>${title}</strong><span>${escapeHtml(meta)}</span></article>`;
    }).join("")
    : `<div class="empty-state">No headlines yet. Ask the chat: “news for Reliance”.</div>`;
}

async function refreshLandingNews() {
  try {
    state.marketNews = await api("/market-news/latest?limit=6");
  } catch (error) {
    // Keep whatever is cached.
  }
  renderLandingNews();
}

function capabilityStatus(label, stateLabel, description, tone = "ready") {
  return `
    <article class="capability-item ${escapeHtml(tone)}">
      <div>
        <strong>${escapeHtml(label)}</strong>
        <p>${escapeHtml(description)}</p>
      </div>
      <span>${escapeHtml(stateLabel)}</span>
    </article>
  `;
}

function renderCommandCenter(documentCount) {
  const platform = state.platformSummary || {};
  const safety = platform.safety || {};
  const assetCoverage = platform.asset_coverage || {};
  const assetsWithData = Object.values(assetCoverage)
    .filter((item) => item.local_data_available).length;
  const openalgoConfigured = Boolean(safety.openalgo_key_configured);
  const liveEnabled = Boolean(safety.live_trading_enabled);
  const checks = state.health?.checks || {};
  const llmProvider = checks.llm_provider || "groq";
  const llmConfigured = llmProvider === "groq"
    ? checks.groq_api_key_configured
    : checks.openai_api_key_configured;
  const datasetCount = state.datasets.length;
  const strategyCount = state.strategies.length;
  const personaCount = state.personas.length;
  const latestRun = platform.latest_completed_run || state.runs[0];

  $("#command-safety").textContent = liveEnabled
    ? "Live trading enabled by configuration"
    : "Live trading disabled; paper/sandbox paths use OpenAlgo analyzer mode";

  $("#capability-list").innerHTML = [
    capabilityStatus(
      "Assistant",
      llmConfigured ? "configured" : "key required",
      "Understands plain-language requests about markets, your account, strategies, and trades. Needs a model key to run.",
      llmConfigured ? "ready" : "attention",
    ),
    capabilityStatus(
      "Market research",
      datasetCount ? "ready" : "needs data",
      `${formatNumber(datasetCount, 0)} dataset(s), ${formatNumber(documentCount, 0)} searchable document(s).`,
      datasetCount ? "ready" : "attention",
    ),
    capabilityStatus(
      "Backtesting",
      strategyCount ? "ready" : "needs strategies",
      `${formatNumber(strategyCount, 0)} strategy engine(s) available. History is fetched automatically when needed.`,
      strategyCount ? "ready" : "attention",
    ),
    capabilityStatus(
      "Paper trading",
      openalgoConfigured ? "credentialed" : "credential required",
      "Paper orders route to your broker's practice account. Needs broker credentials and your approval.",
      openalgoConfigured ? "gated" : "attention",
    ),
    capabilityStatus(
      "Live trading",
      liveEnabled ? "enabled" : "disabled",
      "Live orders are blocked unless enabled, and always require your explicit approval.",
      liveEnabled ? "gated" : "blocked",
    ),
    capabilityStatus(
      "Strategy personas",
      personaCount ? "ready" : "needs profiles",
      `${formatNumber(personaCount, 0)} persona profile(s) that shape strategy bias and risk rules.`,
      personaCount ? "ready" : "attention",
    ),
    capabilityStatus(
      "Multi-asset support",
      `${formatNumber(assetsWithData, 0)} with data`,
      "Equities, derivatives, commodities, and crypto are checked per symbol before use.",
      assetsWithData ? "ready" : "attention",
    ),
  ].join("");

  renderWidgetPicker();
  renderDashboardWidgets(latestRun);
  renderQuickActions();
}

function renderWidgetPicker() {
  const options = [
    ["research", "Research"],
    ["backtests", "Backtests"],
    ["assets", "Assets"],
    ["openalgo", "Broker"],
    ["news", "News"],
    ["personas", "Personas"],
  ];
  $("#widget-picker").innerHTML = options.map(([id, label]) => `
    <label class="widget-toggle">
      <input type="checkbox" value="${escapeHtml(id)}" ${state.dashboardWidgets.includes(id) ? "checked" : ""}>
      <span>${escapeHtml(label)}</span>
    </label>
  `).join("");
  $("#widget-picker").querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => {
      state.dashboardWidgets = [...$("#widget-picker").querySelectorAll("input:checked")]
        .map((item) => item.value);
      localStorage.setItem(
        "iimc_dashboard_widgets",
        JSON.stringify(state.dashboardWidgets),
      );
      persistDashboardPreferences();
      renderDashboardWidgets(state.platformSummary?.latest_completed_run || state.runs[0]);
    });
  });
}

function renderDashboardWidgets(latestRun) {
  const platform = state.platformSummary || {};
  const assetCoverage = platform.asset_coverage || {};
  const assetsSupported = Object.keys(assetCoverage).length;
  const assetsWithData = Object.values(assetCoverage)
    .filter((item) => item.local_data_available).length;
  const widgetData = {
    research: ["Research data", formatNumber(state.datasets.reduce((sum, item) => sum + Number(item.row_count || 0), 0), 0), "Rows in your local data"],
    assets: ["Asset coverage", `${formatNumber(assetsWithData, 0)} / ${formatNumber(assetsSupported, 0)}`, "Architectural support with local data availability tracked"],
    backtests: ["Backtests", formatNumber(state.runs.length, 0), latestRun ? `Latest ${latestRun.strategy || latestRun.strategy_id}` : "No completed run selected"],
    openalgo: ["Broker", state.openalgoMonitor?.status || "checking", state.openalgoMonitor?.message || "Broker connection status"],
    news: ["Market news", state.platformSummary?.market_news?.configured ? "configured" : "not configured", "Live headlines when a provider is set"],
    personas: ["Personas", formatNumber(state.personas.length, 0), "Strategy and risk profiles available to the assistant"],
  };
  $("#dashboard-widgets").innerHTML = state.dashboardWidgets.map((id) => {
    const item = widgetData[id];
    if (!item) return "";
    return `
      <article class="dashboard-widget">
        <span>${escapeHtml(item[0])}</span>
        <strong>${escapeHtml(item[1])}</strong>
        <small>${escapeHtml(item[2])}</small>
      </article>
    `;
  }).join("") || `<div class="empty-state">Select at least one widget.</div>`;
}

function renderPersonas() {
  const container = $("#persona-list");
  if (!container) return;
  container.innerHTML = state.personas.length
    ? state.personas.map((persona) => {
      const assets = (persona.asset_classes || []).join(", ");
      const strategies = (persona.strategy_bias?.preferred_strategies || []).join(", ");
      const riskRules = persona.risk_rules || {};
      const riskParts = [];
      if (riskRules.max_order_value != null) {
        riskParts.push(`Max order ${formatNumber(riskRules.max_order_value, 0)}`);
      }
      if (riskRules.stop_loss_pct != null) {
        riskParts.push(`Stop ${(riskRules.stop_loss_pct * 100).toFixed(1)}%`);
      }
      if (riskRules.requires_approval_for_paper) riskParts.push("Paper approval");
      if (riskRules.requires_approval_for_live) riskParts.push("Live approval");
      const focus = (persona.dashboard_focus || []).join(", ");
      return `
        <article class="persona-item">
          <div>
            <strong>${escapeHtml(persona.name)}</strong>
          </div>
          <p>${escapeHtml(persona.description)}</p>
          <small>Assets: ${escapeHtml(assets || "none")} · Bias: ${escapeHtml(strategies || "none")}</small>
          ${riskParts.length ? `<small>Risk: ${escapeHtml(riskParts.join(" · "))}</small>` : ""}
          ${focus ? `<small>Focus: ${escapeHtml(focus)}</small>` : ""}
        </article>
      `;
    }).join("")
    : `<div class="empty-state">No personas configured.</div>`;
}

function renderQuickActions() {
  const prompts = [
    "What's the price of Reliance?",
    "Backtest an EMA crossover on HDFCBANK",
    "What's my P&L today?",
    "Show my positions",
    "News for Tata Steel",
  ];
  $("#quick-actions").innerHTML = prompts.map((prompt) => `
    <button class="secondary-button quick-action" type="button" data-prompt="${escapeHtml(prompt)}">${escapeHtml(prompt)}</button>
  `).join("");
  $("#quick-actions").querySelectorAll(".quick-action").forEach((button) => {
    button.addEventListener("click", () => {
      $("#chat-input").value = button.dataset.prompt;
      $("#chat-form").requestSubmit();
    });
  });
}


async function loadAccount() {
  let monitor;
  try {
    monitor = await api("/platform/openalgo/monitor");
  } catch (error) {
    monitor = { status: "unavailable", checks: {}, configured: false };
  }
  state.openalgoMonitor = monitor;
  const notConfigured = monitor.configured === false;
  $("#openalgo-notice").textContent = notConfigured
    ? "Connect your broker (OpenAlgo) to see live funds, positions, and orders."
    : (monitor.message || "");
  renderOpenAlgoMonitor(monitor);
  // Watches are independent of live account data — load them regardless.
  loadWatches().catch(() => {});
  document.querySelectorAll(".account-tab").forEach((button) => {
    button.disabled = notConfigured;
  });
  if (notConfigured) {
    $("#account-view").textContent = "Connect your broker to see your account.";
    return;
  }
  const active = document.querySelector(".account-tab.active")?.dataset.account || "funds";
  await loadAccountView(active);
}

const _pickField = (row, ...names) => {
  for (const name of names) {
    if (row[name] !== undefined && row[name] !== null && row[name] !== "") return row[name];
  }
  return null;
};
const _numField = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};
// Ticker as the primary label with the readable company name beneath it.
const _symbolCell = (row) => {
  const ticker = _pickField(row, "symbol", "tradingsymbol", "tsym") || "-";
  const name = row.company_name;
  return `<td><strong>${escapeHtml(String(ticker))}</strong>${
    name ? `<br><small class="row-subname">${escapeHtml(String(name))}</small>` : ""
  }</td>`;
};

function renderAccountData(type, data) {
  if (type === "funds") {
    const d = data || {};
    const cash = _numField(_pickField(d, "availablecash", "available_cash", "cash"));
    const used = _numField(_pickField(d, "utiliseddebits", "utilised_margin", "used_margin"));
    const unreal = _numField(_pickField(d, "m2munrealized", "unrealized_pnl"));
    const real = _numField(_pickField(d, "m2mrealized", "realized_pnl"));
    const row = (label, v) => v === null ? "" : `<div><span>${label}</span><strong>₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong></div>`;
    return `<div class="account-funds">${row("Available cash", cash)}${row("Used margin", used)}${row("Unrealized P&amp;L", unreal)}${row("Realized P&amp;L", real)}</div>`;
  }
  const rows = Array.isArray(data) ? data : [];
  if (!rows.length) {
    const empties = { positionbook: "no open positions", holdings: "no holdings", orderbook: "no orders today", tradebook: "no trades today" };
    return `<div class="empty-state">You have ${empties[type] || "nothing here"}.</div>`;
  }
  const cell = (v) => `<td>${escapeHtml(v == null ? "-" : String(v))}</td>`;
  const money = (v) => v == null ? "-" : "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  if (type === "positionbook" || type === "holdings") {
    let total = 0;
    const body = rows.map((r) => {
      const qty = _numField(_pickField(r, "quantity", "netqty", "qty"));
      const avg = _numField(_pickField(r, "average_price", "averageprice", "avgprice", "buyavgprice"));
      const ltp = _numField(_pickField(r, "ltp", "lastprice", "last_price"));
      let pnl = _numField(_pickField(r, "pnl", "unrealized_pnl", "m2m", "profitandloss"));
      if (pnl == null && qty != null && avg != null && ltp != null) pnl = (ltp - avg) * qty;
      if (pnl != null) total += pnl;
      return `<tr>${_symbolCell(r)}<td>${qty ?? "-"}</td><td>${money(avg)}</td><td>${money(ltp)}</td><td>${money(pnl)}</td></tr>`;
    }).join("");
    return `<div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>LTP</th><th>P&amp;L</th></tr></thead><tbody>${body}</tbody></table></div><p class="account-total">Total P&amp;L: <strong>${money(total)}</strong></p>`;
  }
  if (type === "orderbook") {
    const body = rows.map((r) => `<tr>${_symbolCell(r)}${cell(_pickField(r, "action", "transaction_type", "side"))}<td>${_pickField(r, "quantity", "qty") ?? "-"}</td><td>${money(_numField(_pickField(r, "price", "average_price", "averageprice")))}</td>${cell(_pickField(r, "order_status", "status", "orderstatus"))}</tr>`).join("");
    return `<div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Status</th></tr></thead><tbody>${body}</tbody></table></div>`;
  }
  const body = rows.map((r) => `<tr>${_symbolCell(r)}${cell(_pickField(r, "action", "transaction_type", "side"))}<td>${_pickField(r, "quantity", "qty", "fillsize") ?? "-"}</td><td>${money(_numField(_pickField(r, "average_price", "averageprice", "price", "fillprice")))}</td></tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

async function loadAccountView(type) {
  const box = $("#account-view");
  if (!box) return;
  box.textContent = "Loading...";
  try {
    const payload = await api(`/openalgo/${encodeURIComponent(type)}`);
    box.innerHTML = renderAccountData(type, payload.data);
  } catch (error) {
    box.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

// --- Technical watches (RSI / price-vs-EMA monitors) ---
const _watchConditionText = (condition, threshold) => ({
  rsi_below: `RSI below ${threshold}`,
  rsi_above: `RSI above ${threshold}`,
  price_above_ema20: "price above EMA20",
  price_below_ema20: "price below EMA20",
}[condition] || condition);

function renderWatches(watches) {
  const box = $("#watches-list");
  if (!box) return;
  if (!watches.length) {
    box.innerHTML = `<div class="empty-state">No watches yet. In chat, try “watch RELIANCE for RSI below 30”.</div>`;
    return;
  }
  const rows = watches.map((w) => `<tr>
      <td><strong>${escapeHtml(w.symbol)}</strong></td>
      <td>${escapeHtml(_watchConditionText(w.condition, w.threshold))}</td>
      <td><span class="watch-status watch-${escapeHtml(w.status)}">${escapeHtml(w.status)}</span></td>
      <td>${w.last_value == null ? "—" : escapeHtml(String(w.last_value))}</td>
      <td><button class="secondary-button watch-remove" data-watch-id="${escapeHtml(w.watch_id)}">Remove</button></td>
    </tr>`).join("");
  box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Condition</th><th>Status</th><th>Last</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function loadWatches() {
  const box = $("#watches-list");
  if (!box) return;
  try {
    const payload = await api("/watches");
    renderWatches(payload.watches || []);
  } catch (error) {
    box.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function checkWatches() {
  const button = $("#check-watches");
  if (button) button.disabled = true;
  try {
    const result = await api("/watches/check", { method: "POST" });
    const fired = (result.fired || []).length;
    toast(fired
      ? `${fired} watch(es) fired`
      : `Checked ${result.checked || 0} watch(es); none fired`);
    if ((result.errors || []).length) toast(result.errors[0]);
    await loadWatches();
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

async function removeWatch(watchId) {
  try {
    await api(`/watches/${encodeURIComponent(watchId)}`, { method: "DELETE" });
    await loadWatches();
  } catch (error) {
    toast(error.message);
  }
}

// --- Agents tab (ATL kernel) ---
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
async function loadCoverage() {
  const box = $("#coverage-summary");
  if (!box) return;
  try {
    const c = await api("/data/health");
    const covered = c.symbols.filter((s) => s.has_price_history);
    const gaps = (c.gaps || []).map((g) => `<li>${escapeHtml(g)}</li>`).join("");
    const rows = covered.map((s) => `<tr>
        <td><strong>${escapeHtml(s.symbol)}</strong></td>
        <td>${s.price_rows.toLocaleString("en-IN")} bars</td>
        <td>${escapeHtml((s.intervals || []).join(", ") || "—")}</td>
        <td>${escapeHtml(s.latest_bar || "—")}</td>
        <td>${s.has_fundamentals ? `${s.statement_count} statement(s)` : "—"}</td>
      </tr>`).join("");
    box.innerHTML = `
      <div class="coverage-stats">
        <div><span>Price history</span><strong>${c.with_price_history} / ${c.symbol_count}</strong><small>${c.price_coverage_pct}%</small></div>
        <div><span>Fundamentals</span><strong>${c.with_fundamentals} / ${c.symbol_count}</strong><small>${c.fundamentals_coverage_pct}%</small></div>
      </div>
      ${gaps ? `<ul class="coverage-gaps">${gaps}</ul>` : ""}
      ${covered.length ? `<div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Bars</th><th>Intervals</th><th>Latest</th><th>Fundamentals</th></tr></thead><tbody>${rows}</tbody></table></div>` : ""}`;
  } catch (error) {
    box.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function runBackfill() {
  const button = $("#run-backfill");
  if (button) button.disabled = true;
  try {
    const result = await api("/data/backfill/run", { method: "POST", body: JSON.stringify({ max_symbols: 5 }) });
    const ok = (result.results || []).filter((r) => r.status === "ok").length;
    const failed = (result.results || []).filter((r) => r.status === "failed");
    toast(failed.length
      ? `${ok} imported, ${failed.length} failed (${failed[0].reason || ""})`.slice(0, 140)
      : `${ok} symbol(s) imported · ${result.remaining} remaining`);
    await loadCoverage();
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

// --- Supervisor (autonomous drift watch) ---
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

async function showContestResults(contestId) {
  const box = $("#contest-results-detail");
  if (!box) return;
  box.innerHTML = `<div class="empty-state">Loading results…</div>`;
  try {
    const payload = await api(`/contests/${encodeURIComponent(contestId)}/results`);
    const results = payload.results || [];
    if (!results.length) {
      box.innerHTML = `<div class="empty-state">This contest has no snapshot yet — it is ${escapeHtml(payload.status)}.</div>`;
      return;
    }
    const rows = results.map((r) => `<tr>
        <td>${r.rank ?? "—"}</td>
        <td>${escapeHtml(r.agent_id)}</td>
        <td>${escapeHtml(String(r.composite))}</td>
        <td><small class="row-subname">run ${escapeHtml(r.run_id || "—")}</small></td>
      </tr>`).join("");
    box.innerHTML = `<p class="leaderboard-unranked-head">Frozen standings (dataset ${escapeHtml((payload.dataset_hash || "n/a").slice(0, 12))}…):</p><div class="table-wrap"><table><thead><tr><th>#</th><th>Agent</th><th>Score</th><th>Traces to</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  } catch (error) {
    box.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function runAgent(agentId, agentName, button) {
  const result = $("#agent-run-result");
  const symbol = ($("#agent-symbol")?.value || "RELIANCE").trim().toUpperCase();
  const symbol2 = ($("#agent-symbol2")?.value || "").trim().toUpperCase();
  const body = agentName === "comparator"
    ? { symbols: [symbol, symbol2].filter(Boolean) }
    : { symbol };
  if (button) button.disabled = true;
  result.innerHTML = `<div class="empty-state">Running ${escapeHtml(_agentPretty(agentName))}…</div>`;
  try {
    const payload = await api(`/agents/${encodeURIComponent(agentId)}/run`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    const gaps = (payload.gaps || []).map((g) => `<li>${escapeHtml(g)}</li>`).join("");
    result.innerHTML = `
      <div class="agent-result">
        <p><strong>${escapeHtml(_agentPretty(agentName))}</strong> finished:
          <span class="watch-status watch-${payload.status === "ok" ? "active" : "triggered"}">${escapeHtml(payload.status)}</span>
          · ${(payload.evidence || []).length} evidence item(s)
          · took ${escapeHtml(String(payload.cost?.seconds ?? "?"))}s
          · recorded as ${escapeHtml(payload.run_id)}</p>
        ${gaps ? `<p>Honest gaps:</p><ul>${gaps}</ul>` : ""}
        <details><summary>Full findings</summary><pre>${escapeHtml(JSON.stringify(payload.findings, null, 2))}</pre></details>
      </div>`;
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
async function loadLiveTrades() {
  const panel = $("#live-trades");
  const body = $("#live-trades-body");
  if (!panel || !body) return;
  let positions = [];
  let orders = [];
  try {
    const [pos, ord] = await Promise.all([
      api("/openalgo/positionbook").catch(() => ({ data: [] })),
      api("/openalgo/orderbook").catch(() => ({ data: [] })),
    ]);
    positions = (Array.isArray(pos.data) ? pos.data : []).filter((r) => {
      const qty = _numField(_pickField(r, "quantity", "netqty", "qty"));
      return qty != null && qty !== 0;
    });
    const openStates = ["open", "pending", "trigger pending", "modified"];
    orders = (Array.isArray(ord.data) ? ord.data : []).filter((r) => {
      const status = String(_pickField(r, "order_status", "status", "orderstatus") || "").toLowerCase();
      return openStates.includes(status);
    });
  } catch (error) {
    panel.hidden = true;
    return;
  }
  if (!positions.length && !orders.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const money = (v) => v == null ? "-" : "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  const cell = (v) => `<td>${escapeHtml(v == null ? "-" : String(v))}</td>`;
  let html = "";
  if (positions.length) {
    let total = 0;
    const rows = positions.map((r) => {
      const qty = _numField(_pickField(r, "quantity", "netqty", "qty"));
      const avg = _numField(_pickField(r, "average_price", "averageprice", "avgprice", "buyavgprice"));
      const ltp = _numField(_pickField(r, "ltp", "lastprice", "last_price"));
      let pnl = _numField(_pickField(r, "pnl", "unrealized_pnl", "m2m", "profitandloss"));
      if (pnl == null && qty != null && avg != null && ltp != null) pnl = (ltp - avg) * qty;
      if (pnl != null) total += pnl;
      const cls = pnl == null ? "" : (pnl >= 0 ? "pnl-up" : "pnl-down");
      return `<tr>${_symbolCell(r)}<td>${qty ?? "-"}</td><td>${money(avg)}</td><td>${money(ltp)}</td><td class="${cls}">${money(pnl)}</td></tr>`;
    }).join("");
    html += `<h3>Open positions</h3><div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>LTP</th><th>P&amp;L</th></tr></thead><tbody>${rows}</tbody></table></div><p class="account-total">Total P&amp;L: <strong class="${total >= 0 ? "pnl-up" : "pnl-down"}">${money(total)}</strong></p>`;
  }
  if (orders.length) {
    const rows = orders.map((r) => `<tr>${_symbolCell(r)}${cell(_pickField(r, "action", "transaction_type", "side"))}<td>${_pickField(r, "quantity", "qty") ?? "-"}</td><td>${money(_numField(_pickField(r, "price", "average_price", "averageprice")))}</td>${cell(_pickField(r, "order_status", "status", "orderstatus"))}</tr>`).join("");
    html += `<h3>Working orders</h3><div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  body.innerHTML = html;
}

function renderOpenAlgoMonitor(monitor) {
  const checks = monitor.checks || {};
  const names = ["analyzer", "funds", "orderbook", "tradebook", "positionbook"];
  $("#openalgo-monitor").innerHTML = names.map((name) => {
    const check = checks[name] || { ok: false, status: monitor.status };
    return `
      <article class="openalgo-status-card">
        <strong>${escapeHtml(name)}</strong>
        <span>${check.ok ? "available" : escapeHtml(check.status || "unavailable")}</span>
      </article>
    `;
  }).join("");
}

function renderExperiments() {
  const strategySelect = $("#experiment-strategy");
  const datasetSelect = $("#experiment-dataset");
  strategySelect.innerHTML = state.strategies.length
    ? state.strategies.map((strategy) => `
        <option value="${escapeHtml(strategy.name)}">${escapeHtml(strategy.name)}</option>
      `).join("")
    : `<option value="" disabled selected>No strategies loaded</option>`;
  datasetSelect.innerHTML = marketDatasets().length
    ? marketDatasets().map((dataset) => `
        <option value="${escapeHtml(dataset.dataset_id)}">${escapeHtml(dataset.dataset_id)}</option>
      `).join("")
    : `<option value="" disabled selected>No datasets loaded</option>`;
  const table = $("#experiments-table");
  table.innerHTML = state.experiments.length
    ? state.experiments.map((experiment) => `
      <tr data-experiment-id="${escapeHtml(experiment.experiment_id)}">
        <td>${escapeHtml(experiment.experiment_id)}</td>
        <td>${escapeHtml(experiment.strategy_name)}</td>
        <td><span class="status-pill">${escapeHtml(experiment.status)}</span></td>
        <td>${formatNumber(experiment.candidate_count, 0)}</td>
        <td>${escapeHtml(experiment.verdict || "pending")}</td>
        <td>${escapeHtml(experiment.started_at)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="6">No robustness experiments are stored.</td></tr>`;
  table.querySelectorAll("tr[data-experiment-id]").forEach((row) => {
    row.addEventListener("click", () => loadExperiment(row.dataset.experimentId));
  });
}

function renderBacktestControls() {
  const datasetSelect = $("#backtest-dataset");
  const strategySelect = $("#backtest-strategy");
  if (!datasetSelect || !strategySelect) return;
  datasetSelect.innerHTML = marketDatasets().length
    ? marketDatasets().map((dataset) => `
        <option value="${escapeHtml(dataset.dataset_id)}">${escapeHtml(dataset.dataset_id)}</option>
      `).join("")
    : `<option value="" disabled selected>No datasets loaded</option>`;
  strategySelect.innerHTML = state.strategies.length
    ? state.strategies.map((strategy) => `
        <option value="${escapeHtml(strategy.name)}">${escapeHtml(strategy.name)}</option>
      `).join("")
    : `<option value="" disabled selected>No strategies loaded</option>`;
  renderBacktestParameters(strategySelect.value);
  strategySelect.onchange = () => renderBacktestParameters(strategySelect.value);
  datasetSelect.onchange = () => {
    void loadOptionContracts(datasetSelect.value, "backtest");
  };
  void loadOptionContracts(datasetSelect.value, "backtest");
  renderCustomStrategyControls();
}

function customStrategyTemplate(template) {
  if (template === "external_feature") {
    return {
      indicators: [],
      feature_inputs: [
        {
          name: "news_sentiment",
          dataset_id: "replace_with_feature_dataset_id",
          feature_name: "news_sentiment",
          alignment: "asof",
          max_age_hours: 24,
        },
      ],
      entry_rules: [
        { left: "news_sentiment", operator: ">", right: 0.2, joiner: "AND" },
      ],
      exit_rules: [
        { left: "news_sentiment", operator: "<", right: 0, joiner: "OR" },
      ],
      risk: { max_position_size: 1, stop_loss_pct: 0.02, take_profit_pct: 0.05 },
    };
  }
  if (template === "sma_cross") {
    return {
      indicators: [
        { type: "SMA", period: 20, source: "price" },
        { type: "SMA", period: 50, source: "price" },
      ],
      entry_rules: [
        { left: "SMA_20", operator: "crosses_above", right: "SMA_50", joiner: "AND" },
      ],
      exit_rules: [
        { left: "SMA_20", operator: "crosses_below", right: "SMA_50", joiner: "OR" },
      ],
      risk: { max_position_size: 1, stop_loss_pct: 0.02, take_profit_pct: 0.05 },
    };
  }
  if (template === "ema_rsi") {
    return {
      indicators: [
        { type: "EMA", period: 9, source: "price" },
        { type: "EMA", period: 21, source: "price" },
        { type: "RSI", period: 14, source: "price" },
      ],
      entry_rules: [
        { left: "EMA_9", operator: "crosses_above", right: "EMA_21", joiner: "AND" },
        { left: "RSI_14", operator: "<", right: 65, joiner: "AND" },
      ],
      exit_rules: [
        { left: "EMA_9", operator: "crosses_below", right: "EMA_21", joiner: "OR" },
        { left: "RSI_14", operator: ">", right: 75, joiner: "OR" },
      ],
      risk: { max_position_size: 1, stop_loss_pct: 0.02, take_profit_pct: 0.05 },
    };
  }
  if (template === "momentum") {
    return {
      indicators: [
        { type: "ROC", period: 10, source: "price" },
        { type: "SMA", period: 20, source: "price" },
      ],
      entry_rules: [
        { left: "ROC_10", operator: ">", right: 0, joiner: "AND" },
        { left: "price", operator: ">", right: "SMA_20", joiner: "AND" },
      ],
      exit_rules: [
        { left: "ROC_10", operator: "<=", right: 0, joiner: "OR" },
        { left: "price", operator: "<", right: "SMA_20", joiner: "OR" },
      ],
      risk: { max_position_size: 1, stop_loss_pct: 0.025, take_profit_pct: 0.06 },
    };
  }
  if (template === "macd") {
    return {
      indicators: [
        { name: "MACD_LINE", type: "MACD", source: "price", fast_period: 12, slow_period: 26, signal_period: 9 },
        { name: "MACD_SIGNAL", type: "MACD_SIGNAL", source: "price", fast_period: 12, slow_period: 26, signal_period: 9 },
      ],
      entry_rules: [
        { left: "MACD_LINE", operator: "crosses_above", right: "MACD_SIGNAL", joiner: "AND" },
      ],
      exit_rules: [
        { left: "MACD_LINE", operator: "crosses_below", right: "MACD_SIGNAL", joiner: "OR" },
      ],
      risk: { max_position_size: 1, stop_loss_pct: 0.025, take_profit_pct: 0.06 },
    };
  }
  if (template === "bollinger") {
    return {
      indicators: [
        { name: "BB_UPPER", type: "BB_UPPER", period: 20, source: "price", stddev: 2 },
        { name: "BB_MIDDLE", type: "BB_MIDDLE", period: 20, source: "price", stddev: 2 },
        { name: "BB_LOWER", type: "BB_LOWER", period: 20, source: "price", stddev: 2 },
      ],
      entry_rules: [
        { left: "price", operator: "crosses_above", right: "BB_MIDDLE", joiner: "AND" },
      ],
      exit_rules: [
        { left: "price", operator: "crosses_below", right: "BB_MIDDLE", joiner: "OR" },
      ],
      risk: { max_position_size: 1, stop_loss_pct: 0.02, take_profit_pct: 0.05 },
    };
  }
  if (template === "vwap") {
    return {
      indicators: [{ type: "VWAP", source: "price" }],
      entry_rules: [
        { left: "price", operator: "crosses_above", right: "VWAP", joiner: "AND" },
      ],
      exit_rules: [
        { left: "price", operator: "crosses_below", right: "VWAP", joiner: "OR" },
      ],
      risk: { max_position_size: 1, stop_loss_pct: 0.02, take_profit_pct: 0.05 },
    };
  }
  return {
    indicators: [
      { type: "EMA", period: 9, source: "price" },
      { type: "EMA", period: 21, source: "price" },
    ],
    entry_rules: [
      { left: "EMA_9", operator: "crosses_above", right: "EMA_21", joiner: "AND" },
    ],
    exit_rules: [
      { left: "EMA_9", operator: "crosses_below", right: "EMA_21", joiner: "OR" },
    ],
    risk: { max_position_size: 1, stop_loss_pct: 0.02, take_profit_pct: 0.05 },
  };
}

function customStrategyPayloadFromForm() {
  const template = customStrategyTemplate($("#custom-strategy-template").value);
  const rawRules = $("#custom-strategy-rules").value.trim();
  const rules = rawRules ? JSON.parse(rawRules) : template;
  if (!rules || typeof rules !== "object" || Array.isArray(rules)) {
    throw new Error("Rules must be a JSON object");
  }
  return {
    name: $("#custom-strategy-name").value.trim(),
    description: $("#custom-strategy-description").value.trim(),
    symbol: $("#custom-strategy-symbol").value.trim(),
    timeframe: $("#custom-strategy-timeframe").value.trim(),
    ...template,
    ...rules,
    position_side: $("#custom-strategy-side").value,
    created_by: state.principal?.username || "local_ui",
  };
}

function syncCustomStrategyRules() {
  const editor = $("#custom-strategy-rules");
  if (!editor) return;
  editor.value = JSON.stringify(
    customStrategyTemplate($("#custom-strategy-template").value),
    null,
    2,
  );
}

function renderCustomStrategyControls() {
  const datasetSelect = $("#custom-strategy-dataset");
  const specSelect = $("#custom-strategy-spec-select");
  if (!datasetSelect || !specSelect) return;
  const currentDataset = datasetSelect.value;
  datasetSelect.innerHTML = marketDatasets().length
    ? marketDatasets().map((dataset) => `
        <option value="${escapeHtml(dataset.dataset_id)}">${escapeHtml(dataset.dataset_id)}</option>
      `).join("")
    : `<option value="" disabled selected>No datasets loaded</option>`;
  if (currentDataset) datasetSelect.value = currentDataset;
  datasetSelect.onchange = () => {
    void loadOptionContracts(datasetSelect.value, "custom");
  };
  void loadOptionContracts(datasetSelect.value, "custom");

  const currentSpec = specSelect.value;
  specSelect.innerHTML = state.customStrategySpecs.length
    ? state.customStrategySpecs.map((spec) => `
      <option value="${escapeHtml(spec.spec_id)}">
        ${escapeHtml(spec.name)} - ${escapeHtml(spec.status)}
      </option>
    `).join("")
    : `<option value="">No saved custom specs</option>`;
  if (currentSpec) specSelect.value = currentSpec;
  renderSelectedCustomStrategySpec();
}

function renderSelectedCustomStrategySpec() {
  const specId = $("#custom-strategy-spec-select")?.value;
  const detail = $("#custom-strategy-json");
  const status = $("#custom-strategy-status");
  if (!detail || !status) return;
  const spec = state.customStrategySpecs.find((item) => item.spec_id === specId);
  if (!spec) {
    detail.textContent = "No custom strategy spec selected.";
    status.textContent = "Create or select a custom strategy spec.";
    status.className = "custom-strategy-status";
    return;
  }
  detail.textContent = JSON.stringify(spec, null, 2);
  const missing = spec.missing_capabilities || [];
  status.textContent = missing.length
    ? `Requires review: ${missing.map((item) => item.name || item.kind).join(", ")}`
    : `Executable by native rule-spec runtime: ${spec.spec_id}`;
  status.className = `custom-strategy-status ${missing.length ? "attention" : "ready"}`;
  const deleteButton = $("#delete-custom-strategy");
  if (deleteButton) deleteButton.disabled = !spec;
}

async function deleteCustomStrategySpec() {
  const specId = $("#custom-strategy-spec-select")?.value;
  if (!specId) return;
  const spec = state.customStrategySpecs.find((item) => item.spec_id === specId);
  if (!spec) return;
  if (!window.confirm(`Delete strategy spec "${spec.name}" (${specId})? This cannot be undone.`)) return;
  const button = $("#delete-custom-strategy");
  if (button) button.disabled = true;
  try {
    await api(`/custom-strategy-specs/${encodeURIComponent(specId)}`, {
      method: "DELETE",
    });
    state.customStrategySpecs = state.customStrategySpecs.filter((item) => item.spec_id !== specId);
    renderCustomStrategyControls();
    toast(`Deleted strategy spec ${specId}`);
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

async function submitCustomStrategySpec(event) {
  event.preventDefault();
  const button = $("#create-custom-strategy");
  button.disabled = true;
  try {
    const created = await api("/custom-strategy-specs", {
      method: "POST",
      body: JSON.stringify(customStrategyPayloadFromForm()),
    });
    state.customStrategySpecs = [
      created,
      ...state.customStrategySpecs,
    ].filter((item, index, items) => (
      items.findIndex((candidate) => candidate.spec_id === item.spec_id) === index
    ));
    renderCustomStrategyControls();
    $("#custom-strategy-spec-select").value = created.spec_id;
    renderSelectedCustomStrategySpec();
    toast(`Custom strategy ${created.spec_id} stored`);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function validateCustomStrategySpec() {
  const button = $("#validate-custom-strategy");
  const status = $("#custom-strategy-status");
  button.disabled = true;
  try {
    const result = await api("/custom-strategy-specs/validate", {
      method: "POST",
      body: JSON.stringify(customStrategyPayloadFromForm()),
    });
    const missing = result.missing_capabilities || [];
    status.textContent = missing.length
      ? `Requires review: ${missing.map((item) => item.value || item.kind).join(", ")}`
      : "These rules are ready to backtest.";
    status.className = `custom-strategy-status ${missing.length ? "attention" : "ready"}`;
  } catch (error) {
    status.textContent = error.message;
    status.className = "custom-strategy-status attention";
  } finally {
    button.disabled = false;
  }
}

async function runSelectedCustomStrategySpec() {
  const button = $("#run-custom-strategy");
  const resultBox = $("#backtest-result-json");
  const specId = $("#custom-strategy-spec-select").value;
  if (!specId) {
    toast("Select a custom strategy spec first");
    return;
  }
  button.disabled = true;
  resultBox.classList.remove("hidden");
  resultBox.textContent = "Running custom rule-spec backtest...";
  try {
    const payload = await api(`/custom-strategy-specs/${encodeURIComponent(specId)}/backtest`, {
      method: "POST",
      body: JSON.stringify({
        dataset_id: $("#custom-strategy-dataset").value,
        execution_mode: "research",
        requested_quantity: 1,
        slippage_bps: Number($("#backtest-slippage").value || 0.5),
        instrument: optionContractPayload("custom"),
      }),
    });
    resultBox.textContent = JSON.stringify({
      ...payload,
      data_source: "real",
      execution_note: "Native rule-spec runtime; no generated code executed.",
    }, null, 2);
    toast(`Custom strategy backtest ${payload.run_id} completed`);
    await loadOverview();
    if (payload.run_id) await loadRun(payload.run_id);
  } catch (error) {
    resultBox.textContent = JSON.stringify(
      {
        ok: false,
        safe_failure: true,
        message: error.message,
        no_synthetic_fallback: true,
      },
      null,
      2,
    );
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

function renderBacktestParameters(strategyName) {
  const strategy = state.strategies.find((item) => item.name === strategyName);
  const schema = strategy?.parameter_schema || {};
  const container = $("#backtest-parameters");
  if (!container) return;
  const entries = Object.entries(schema);
  container.innerHTML = entries.length
    ? entries.map(([name, meta]) => {
      const type = meta.type || "string";
      const common = `class="backtest-parameter" data-parameter-name="${escapeHtml(name)}" data-parameter-type="${escapeHtml(type)}"`;
      if (Array.isArray(meta.enum)) {
        return `
          <label>
            <span>${escapeHtml(name.replaceAll("_", " "))}</span>
            <select ${common}>
              ${meta.enum.map((value) => `<option value="${escapeHtml(value)}" ${value === meta.default ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}
            </select>
          </label>
        `;
      }
      if (type === "boolean") {
        return `
          <label>
            <span>${escapeHtml(name.replaceAll("_", " "))}</span>
            <input ${common} type="checkbox" ${meta.default ? "checked" : ""}>
          </label>
        `;
      }
      if (type === "string") {
        return `
          <label>
            <span>${escapeHtml(name.replaceAll("_", " "))}</span>
            <input ${common} type="text" value="${escapeHtml(meta.default ?? "")}" required>
          </label>
        `;
      }
      const step = type === "integer" ? "1" : "0.001";
      return `
        <label>
          <span>${escapeHtml(name.replaceAll("_", " "))}</span>
          <input ${common}
            type="number"
            step="${escapeHtml(step)}"
            min="${escapeHtml(meta.minimum ?? "")}"
            max="${escapeHtml(meta.maximum ?? "")}"
            value="${escapeHtml(meta.default ?? "")}"
            required>
        </label>
      `;
    }).join("")
    : `<div class="empty-state">No configurable parameters.</div>`;
}

async function loadOptionContracts(datasetId, prefix) {
  const field = $(`#${prefix}-option-contract-field`);
  const select = $(`#${prefix}-option-contract`);
  if (!field || !select || !datasetId) return;
  select.dataset.datasetId = datasetId;
  select.disabled = true;
  field.classList.add("hidden");
  try {
    const result = await api(`/datasets/${encodeURIComponent(datasetId)}/instruments?limit=500`);
    if (select.dataset.datasetId !== datasetId) return;
    const instruments = result.instruments || [];
    if (!result.requires_instrument_selection || !instruments.length) {
      select.innerHTML = "";
      return;
    }
    select.innerHTML = instruments.map((instrument) => {
      const value = escapeHtml(JSON.stringify({
        expiry: instrument.expiry,
        strike: instrument.strike,
        option_type: instrument.option_type,
      }));
      const label = `${instrument.expiry} ${formatNumber(instrument.strike, 2)} ${instrument.option_type} (${formatNumber(instrument.candle_count, 0)} candles)`;
      return `<option value="${value}">${escapeHtml(label)}</option>`;
    }).join("");
    select.disabled = false;
    field.classList.remove("hidden");
  } catch (error) {
    if (select.dataset.datasetId !== datasetId) return;
    select.innerHTML = "";
    console.warn("Option contract list unavailable", error);
  }
}

function optionContractPayload(prefix) {
  const selected = $(`#${prefix}-option-contract`)?.value;
  if (!selected) return null;
  try {
    return JSON.parse(selected);
  } catch {
    throw new Error("The selected option contract is invalid. Refresh the dataset and try again.");
  }
}

async function submitExperiment(event) {
  event.preventDefault();
  const button = $("#run-experiment");
  button.disabled = true;
  try {
    const parameterGrid = JSON.parse($("#experiment-grid").value);
    if (!Array.isArray(parameterGrid)) {
      throw new Error("Parameter candidates must be a JSON array");
    }
    const task = await api("/experiments/robustness/submit", {
      method: "POST",
      body: JSON.stringify({
        strategy_name: $("#experiment-strategy").value,
        dataset_id: $("#experiment-dataset").value,
        parameter_grid: parameterGrid,
        split_ratio: Number($("#experiment-split").value),
        fee_bps: Number($("#experiment-fee").value),
        slippage_bps: Number($("#experiment-slippage").value),
        persist_selected_runs: true,
      }),
    });
    toast(`Experiment task ${task.task_id} queued`);
    await waitForExperimentTask(task.task_id);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function waitForExperimentTask(taskId) {
  const deadline = Date.now() + 10 * 60 * 1000;
  while (Date.now() < deadline) {
    const task = await api(`/tasks/${encodeURIComponent(taskId)}`);
    if (task.status === "succeeded") {
      toast(`Experiment ${task.result.experiment_id} completed`);
      state.operations = null;
      await loadOverview();
      await loadExperiment(task.result.experiment_id);
      return;
    }
    if (task.status === "failed") {
      throw new Error(task.error_message || "Experiment task failed");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 2000));
  }
  throw new Error("Experiment is still running; inspect Tasks or refresh later");
}

async function loadExperiment(experimentId) {
  try {
    const experiment = await api(
      `/experiments/robustness/${encodeURIComponent(experimentId)}`,
    );
    const detail = $("#experiment-detail");
    detail.classList.remove("hidden");
    const selected = experiment.summary?.selected_test_metrics || {};
    detail.innerHTML = `
      <div class="section-heading">
        <div>
          <h2>${escapeHtml(experiment.experiment_id)}</h2>
          <p>${escapeHtml(experiment.strategy_name)} on ${escapeHtml(experiment.dataset_id)}</p>
        </div>
        <div class="section-actions">
          <span class="status-pill">${escapeHtml(experiment.verdict)}</span>
          ${hasRole("researcher") ? `<button class="secondary-button" id="generate-experiment-report">Generate report</button>` : ""}
        </div>
      </div>
      <div class="detail-grid">
        <div class="detail-cell"><span>Test return</span><strong>${formatNumber(selected.return_pct, 4)}%</strong></div>
        <div class="detail-cell"><span>Test profit factor</span><strong>${formatNumber(selected.profit_factor, 4)}</strong></div>
        <div class="detail-cell"><span>Test trades</span><strong>${formatNumber(selected.total_trades, 0)}</strong></div>
        <div class="detail-cell"><span>Profitable candidates</span><strong>${formatNumber((experiment.summary?.profitable_test_candidate_ratio || 0) * 100, 1)}%</strong></div>
      </div>
      <div class="table-wrap experiment-trials">
        <table>
          <thead><tr><th>Candidate</th><th>Selected</th><th>Parameters</th><th>Train return</th><th>Test return</th><th>Test PF</th></tr></thead>
          <tbody>${experiment.trials.map((trial) => `
            <tr>
              <td>${trial.candidate_index}</td>
              <td>${trial.selected ? "yes" : "no"}</td>
              <td><code>${escapeHtml(JSON.stringify(trial.parameters))}</code></td>
              <td>${formatNumber(trial.train_metrics.return_pct, 4)}%</td>
              <td>${formatNumber(trial.test_metrics.return_pct, 4)}%</td>
              <td>${formatNumber(trial.test_metrics.profit_factor, 4)}</td>
            </tr>
          `).join("")}</tbody>
        </table>
      </div>
    `;
    $("#generate-experiment-report")?.addEventListener("click", async () => {
      try {
        const report = await api(
          `/experiments/robustness/${encodeURIComponent(experimentId)}/reports`,
          { method: "POST" },
        );
        toast(`Report ${report.report_id} generated`);
      } catch (error) {
        toast(error.message);
      }
    });
  } catch (error) {
    toast(error.message);
  }
}

function hasRole(required) {
  const rank = { viewer: 1, researcher: 2, approver: 3, admin: 4 };
  return (rank[state.principal?.role] || 0) >= rank[required];
}

function renderMarkdownInline(segment) {
  return segment
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noreferrer">$1</a>',
    );
}

function splitMarkdownTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "")
    .split("|").map((cell) => cell.trim());
}

function renderMarkdown(raw) {
  const codeBlocks = [];
  const withPlaceholders = String(raw ?? "").replace(
    /```\w*\n?([\s\S]*?)```/g,
    (match, code) => {
      codeBlocks.push(
        `<pre class="md-code"><code>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`,
      );
      return `@@MDCODE${codeBlocks.length - 1}@@`;
    },
  );
  const lines = escapeHtml(withPlaceholders).split("\n");
  const html = [];
  let listType = null;
  const closeList = () => {
    if (listType) {
      html.push(`</${listType}>`);
      listType = null;
    }
  };
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const placeholder = line.trim().match(/^@@MDCODE(\d+)@@$/);
    if (placeholder) {
      closeList();
      html.push(codeBlocks[Number(placeholder[1])] || "");
      index += 1;
      continue;
    }
    if (
      line.includes("|")
      && index + 1 < lines.length
      && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[index + 1])
    ) {
      closeList();
      const headers = splitMarkdownTableRow(line);
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].includes("|")) {
        rows.push(splitMarkdownTableRow(lines[index]));
        index += 1;
      }
      html.push(
        '<table class="md-table"><thead><tr>'
        + headers.map((cell) => `<th>${renderMarkdownInline(cell)}</th>`).join("")
        + "</tr></thead><tbody>"
        + rows.map((cells) => (
          "<tr>" + cells.map((cell) => `<td>${renderMarkdownInline(cell)}</td>`).join("") + "</tr>"
        )).join("")
        + "</tbody></table>",
      );
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 2, 6);
      html.push(`<h${level}>${renderMarkdownInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    if (bullet) {
      if (listType !== "ul") {
        closeList();
        html.push("<ul>");
        listType = "ul";
      }
      html.push(`<li>${renderMarkdownInline(bullet[1])}</li>`);
      index += 1;
      continue;
    }
    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ordered) {
      if (listType !== "ol") {
        closeList();
        html.push("<ol>");
        listType = "ol";
      }
      html.push(`<li>${renderMarkdownInline(ordered[1])}</li>`);
      index += 1;
      continue;
    }
    if (!line.trim()) {
      closeList();
      index += 1;
      continue;
    }
    closeList();
    html.push(`<p>${renderMarkdownInline(line)}</p>`);
    index += 1;
  }
  closeList();
  return html.join("");
}

function appendMessage(role, content, className = "", options = {}) {
  const article = document.createElement("article");
  article.className = `message ${role} ${className}`.trim();
  const body = role === "assistant" && !className.includes("error")
    ? renderMarkdown(content)
    : `<p>${escapeHtml(content)}</p>`;
  const time = new Date().toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit",
  });
  article.innerHTML = `
    <div class="message-role">${role === "user" ? "You" : "Assistant"}
      <span class="message-time">${time}</span>
    </div>
    <div class="message-body">${body}</div>
  `;
  if (options.stream && role === "assistant") {
    streamMessageBody(article.querySelector(".message-body"));
  }
  if (role === "assistant" && navigator.clipboard) {
    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.className = "copy-message";
    copyButton.title = "Copy message";
    copyButton.textContent = "Copy";
    copyButton.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(content);
        copyButton.textContent = "Copied";
        setTimeout(() => { copyButton.textContent = "Copy"; }, 1500);
      } catch (error) {
        toast("Clipboard unavailable");
      }
    });
    article.appendChild(copyButton);
  }
  $("#messages").appendChild(article);
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function streamMessageBody(body) {
  // Progressive reveal so answers read like a live assistant response.
  const blocks = [...body.children];
  if (blocks.length <= 1) return;
  blocks.forEach((block) => { block.style.display = "none"; });
  let index = 0;
  const revealNext = () => {
    if (index >= blocks.length) return;
    blocks[index].style.display = "";
    index += 1;
    const container = $("#messages");
    if (container) container.scrollTop = container.scrollHeight;
    setTimeout(revealNext, 140);
  };
  revealNext();
}

function showTypingIndicator() {
  const article = document.createElement("article");
  article.className = "message assistant typing";
  article.innerHTML = `
    <div class="message-role">Assistant</div>
    <div class="typing-dots"><span></span><span></span><span></span></div>
  `;
  $("#messages").appendChild(article);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return article;
}

function exportChatMarkdown() {
  const sections = [...document.querySelectorAll("#messages .message")]
    .filter((message) => !message.classList.contains("typing"))
    .map((message) => {
      const role = message.classList.contains("user") ? "You" : "Assistant";
      const text = message.querySelector(".message-body, p")?.innerText || "";
      return `**${role}:**\n\n${text}`;
    });
  const markdown = `# IIMC Trading Assistant conversation\n\nSession: ${state.sessionId}\n\n${sections.join("\n\n---\n\n")}\n`;
  const blob = new Blob([markdown], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${state.sessionId}.md`;
  anchor.click();
  URL.revokeObjectURL(url);
}

async function restoreChatHistory() {
  try {
    const payload = await api(
      `/sessions/${encodeURIComponent(state.sessionId)}/messages?limit=100`,
    );
    const messages = (payload.messages || []).filter(
      (entry) => entry.role === "user" || entry.role === "assistant",
    );
    if (!messages.length) return;
    $("#messages").innerHTML = "";
    messages.forEach((entry) => appendMessage(entry.role, entry.content));
  } catch (error) {
    // Best effort: keep the default welcome message when history is empty.
  }
}

async function submitChat(event) {
  event.preventDefault();
  const input = $("#chat-input");
  const message = input.value.trim();
  if (!message) return;
  appendMessage("user", message);
  input.value = "";
  input.style.height = "auto";
  $("#send-button").disabled = true;
  const typing = showTypingIndicator();
  try {
    const payload = await api("/chat", {
      method: "POST",
      body: JSON.stringify({ session_id: state.sessionId, message }),
    });
    typing.remove();
    appendMessage("assistant", payload.answer, "", { stream: true });
    maybeRenderApprovalPrompt(payload);
    if (["get_openalgo_snapshot", "get_openalgo_monitor"].includes(payload.intent)) {
      try {
        await loadAccount();
      } catch (error) {
        toast(`Account view refresh paused: ${error.message}`);
      }
    }
    const tradeIntents = [
      "prepare_direct_order", "approve_pending_order", "square_off_all",
      "cancel_all_orders", "submit_sandbox_order_intent", "get_openalgo_snapshot",
    ];
    if (tradeIntents.includes(payload.intent)) {
      loadLiveTrades().catch(() => {});
    }
    if (String(payload.intent || "").includes("news")) {
      refreshLandingNews().catch(() => {});
    }
  } catch (error) {
    typing.remove();
    appendMessage("assistant", error.message, "error");
  } finally {
    $("#send-button").disabled = false;
    input.focus();
  }
}

// When the assistant prepares an order, show clickable Approve / Cancel
// buttons right in the chat so the client never leaves the conversation.
function maybeRenderApprovalPrompt(payload) {
  const orderIntents = ["prepare_direct_order", "prepare_live_order_intent"];
  if (!orderIntents.includes(payload.intent)) return;
  const data = payload.data || {};
  const intentId = data.intent_id;
  const pending = data.status === "pending_approval" || data.approval_id;
  if (!intentId || !pending) return;
  const isLive = payload.intent === "prepare_live_order_intent";
  const summary = [data.side, data.quantity, data.symbol]
    .filter(Boolean).join(" ");

  const prompt = document.createElement("div");
  prompt.className = `approval-prompt${isLive ? " live" : ""}`;
  prompt.innerHTML = `
    <div class="approval-text">
      ${isLive ? "⚠ <strong>LIVE order</strong> — real money. " : ""}
      Approve ${summary ? `<strong>${escapeHtml(summary)}</strong>` : "this order"}?
    </div>
    <div class="approval-actions">
      <button type="button" class="primary-button approval-approve">✓ Approve &amp; send</button>
      <button type="button" class="secondary-button approval-cancel">Cancel</button>
    </div>
  `;
  $("#messages").appendChild(prompt);
  $("#messages").scrollTop = $("#messages").scrollHeight;

  const disable = () => prompt.querySelectorAll("button").forEach((b) => (b.disabled = true));
  prompt.querySelector(".approval-approve").addEventListener("click", () => {
    if (isLive && !window.confirm(
      `Place a REAL live order (${summary || "this order"}) with real money?`,
    )) return;
    disable();
    prompt.classList.add("resolved");
    $("#chat-input").value = `approve ${intentId}`;
    $("#chat-form").requestSubmit();
  });
  prompt.querySelector(".approval-cancel").addEventListener("click", () => {
    disable();
    prompt.classList.add("resolved");
    prompt.querySelector(".approval-text").innerHTML =
      "Cancelled — nothing was sent to your broker.";
  });
}

function renderRuns() {
  renderBacktestControls();
  const table = $("#runs-table");
  if (!state.runs.length) {
    table.innerHTML = `<tr><td colspan="8">No strategy runs are stored.</td></tr>`;
    return;
  }
  table.innerHTML = state.runs.map((run) => `
    <tr data-run-id="${escapeHtml(run.run_id)}">
      <td><input class="run-selector" type="checkbox" aria-label="Select ${escapeHtml(run.run_id)}"></td>
      <td>${escapeHtml(run.run_id)}</td>
      <td>${escapeHtml(run.strategy)}</td>
      <td><span class="status-pill">${escapeHtml(run.status)}</span></td>
      <td>${formatNumber(run.total_trades, 0)}</td>
      <td>${formatNumber(run.net_pnl)}</td>
      <td>${formatNumber(run.max_drawdown)}</td>
      <td>${formatNumber(run.return_pct, 4)}%</td>
    </tr>
  `).join("");
  table.querySelectorAll("tr[data-run-id]").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (!event.target.matches(".run-selector")) loadRun(row.dataset.runId);
    });
    row.querySelector(".run-selector").addEventListener("change", (event) => {
      if (event.target.checked) state.selectedRuns.add(row.dataset.runId);
      else state.selectedRuns.delete(row.dataset.runId);
      $("#compare-runs").disabled = state.selectedRuns.size < 2
        || state.selectedRuns.size > 10;
    });
  });
}

async function submitBacktest(event) {
  event.preventDefault();
  const button = $("#run-backtest");
  const resultBox = $("#backtest-result-json");
  button.disabled = true;
  button.textContent = "Running...";
  resultBox.classList.remove("hidden");
  resultBox.textContent = "Running IIMC historical backtest...";
  try {
    const strategyName = $("#backtest-strategy").value;
    const parameters = Object.fromEntries(
      [...document.querySelectorAll(".backtest-parameter")].map((input) => {
        const type = input.dataset.parameterType;
        const value = type === "boolean"
          ? input.checked
          : (type === "integer" || type === "number" ? Number(input.value) : input.value);
        return [input.dataset.parameterName, value];
      }),
    );
    const payload = await api("/platform/backtest/run", {
      method: "POST",
      body: JSON.stringify({
        dataset_id: $("#backtest-dataset").value,
        strategy_name: strategyName,
        execution_mode: $("#backtest-mode").value,
        parameters,
        slippage_bps: Number($("#backtest-slippage").value),
        instrument: optionContractPayload("backtest"),
      }),
    });
    resultBox.textContent = JSON.stringify({
      ...payload,
      data_source: "real",
      visible_in_openalgo: false,
      explanation: payload.execution_mode === "semi_auto"
        ? "This paper run recorded an approved order; the actual broker submission is a separate step."
        : "This is a historical backtest, not live broker activity.",
    }, null, 2);
    toast(`Backtest ${payload.run_id} completed`);
    await loadOverview();
    if (payload.run_id) await loadRun(payload.run_id);
  } catch (error) {
    resultBox.textContent = JSON.stringify(
      {
        ok: false,
        safe_failure: true,
        message: error.message,
        no_synthetic_fallback: true,
      },
      null,
      2,
    );
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Run backtest";
  }
}

function renderPerformanceMetricCards(summary = {}) {
  const metrics = [
    ["Total trades", formatNumber(summary.total_trades, 0), "Closed simulated fills"],
    ["Win rate", `${formatNumber(summary.win_rate_pct, 2)}%`, "Winning trades / total trades"],
    ["Profit factor", formatNumber(summary.profit_factor, 4), "Gross profit / gross loss"],
    ["Sharpe", formatNumber(summary.sharpe_ratio, 4), summary.risk_metric_basis || "Daily realized returns"],
    ["Sortino", formatNumber(summary.sortino_ratio, 4), "Downside-adjusted return"],
    ["Recovery", formatNumber(summary.recovery_factor, 4), "Net P&L / max drawdown"],
    ["Expectancy", formatNumber(summary.expectancy, 2), "Average P&L per closed trade"],
    ["Average win", formatNumber(summary.average_win, 2), "Mean profitable trade"],
    ["Average loss", formatNumber(summary.average_loss, 2), "Mean losing trade"],
    ["Total fees", formatNumber(summary.total_fees, 2), "Simulated brokerage/slippage costs"],
    ["Starting equity", formatNumber(summary.starting_equity, 2), "Research capital base"],
    ["Ending equity", formatNumber(summary.ending_equity, 2), "Starting equity plus net P&L"],
  ];
  return metrics.map(([label, value, help]) => `
    <article class="performance-metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(help)}</small>
    </article>
  `).join("");
}

async function loadRun(runId) {
  try {
    const [run, performance, timeline] = await Promise.all([
      api(`/runs/${encodeURIComponent(runId)}`),
      api(`/runs/${encodeURIComponent(runId)}/performance`),
      api(`/runs/${encodeURIComponent(runId)}/timeline`),
    ]);
    const detail = $("#run-detail");
    const performanceSummary = performance.summary || {};
    detail.classList.remove("hidden");
    detail.innerHTML = `
      <div class="section-heading">
        <div><h2>${escapeHtml(run.run_id)}</h2><p>${escapeHtml(run.strategy)} on ${escapeHtml(run.dataset_id)}</p></div>
        <div class="section-actions">
          <span class="status-pill">${escapeHtml(run.status)}</span>
          ${hasRole("researcher") ? `<button class="secondary-button" id="generate-report">Generate report</button>` : ""}
        </div>
      </div>
      <div class="detail-grid">
        <div class="detail-cell"><span>Signals</span><strong>${formatNumber(timeline.counts.signals, 0)}</strong></div>
        <div class="detail-cell"><span>Risk decisions</span><strong>${formatNumber(timeline.counts.risk_decisions, 0)}</strong></div>
        <div class="detail-cell"><span>Orders / fills</span><strong>${formatNumber(timeline.counts.orders, 0)} / ${formatNumber(timeline.counts.fills, 0)}</strong></div>
        <div class="detail-cell"><span>Net P&amp;L</span><strong>${formatNumber(run.net_pnl)}</strong></div>
      </div>
      <section class="performance-board">
        <div class="section-heading">
          <div>
            <h3>Performance Metrics</h3>
            <p>Summary from the completed backtest.</p>
          </div>
        </div>
        <div class="performance-metric-grid">
          ${renderPerformanceMetricCards(performanceSummary)}
        </div>
      </section>
      <div class="chart-grid">
        <section class="chart-panel">
          <div><strong>Equity Curve</strong><span>Fill-level simulated equity</span></div>
          <canvas class="curve" id="equity-curve" width="900" height="190" aria-label="Equity curve"></canvas>
        </section>
        <section class="chart-panel">
          <div><strong>Activity Counts</strong><span>Signals, orders, and fills</span></div>
          <canvas class="curve" id="workflow-chart" width="420" height="190" aria-label="Workflow counts"></canvas>
        </section>
        <section class="chart-panel">
          <div><strong>Performance Shape</strong><span>P&L against drawdown</span></div>
          <canvas class="curve" id="pnl-chart" width="420" height="190" aria-label="P and L bars"></canvas>
        </section>
      </div>
      <section class="workflow-timeline">
        <div class="section-heading">
          <div>
            <h3>Signal-to-fill timeline</h3>
            <p>Every step of the backtest in order, from signal to fill.</p>
          </div>
          ${timeline.events.length > 40 ? `<button class="secondary-button" id="show-all-events">Show all ${formatNumber(timeline.events.length, 0)}</button>` : ""}
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Type</th><th>Market time</th><th>Entity</th><th>Decision / state</th></tr>
            </thead>
            <tbody id="timeline-events">
              ${renderTimelineRows(timeline.events.slice(0, 40))}
            </tbody>
          </table>
        </div>
      </section>
    `;
    drawCurve(performance.equity_curve || []);
    drawWorkflowChart(timeline.counts || {});
    drawPnlChart(run);
    $("#generate-report")?.addEventListener("click", (event) => generateReport(runId, event.currentTarget));
    $("#show-all-events")?.addEventListener("click", (event) => {
      $("#timeline-events").innerHTML = renderTimelineRows(timeline.events);
      event.currentTarget.remove();
    });
  } catch (error) {
    toast(error.message);
  }
}

async function generateReport(runId, button) {
  if (button) button.disabled = true;
  try {
    const report = await api(`/runs/${encodeURIComponent(runId)}/reports`, {
      method: "POST",
    });
    toast(`Report ${report.report_id} generated`);
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

async function compareSelectedRuns() {
  const button = $("#compare-runs");
  button.disabled = true;
  try {
    const comparison = await api("/runs/compare", {
      method: "POST",
      body: JSON.stringify({ run_ids: [...state.selectedRuns] }),
    });
    const detail = $("#run-detail");
    detail.classList.remove("hidden");
    detail.innerHTML = `
      <div class="section-heading">
        <div><h2>Run comparison</h2><p>${escapeHtml(comparison.ranking_method)}</p></div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Rank</th><th>Run</th><th>Strategy</th><th>Return</th><th>Net P&amp;L</th><th>Drawdown</th></tr></thead>
          <tbody>${comparison.ranking.map((item) => `
            <tr><td>${item.rank}</td><td>${escapeHtml(item.run_id)}</td><td>${escapeHtml(item.strategy)}</td><td>${formatNumber(item.return_pct, 4)}%</td><td>${formatNumber(item.net_pnl)}</td><td>${formatNumber(item.max_drawdown)}</td></tr>
          `).join("")}</tbody>
        </table>
      </div>
    `;
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}


function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawCurve(points) {
  const canvas = $("#equity-curve");
  if (!canvas) return;
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (!points.length) {
    context.fillStyle = cssVar("--muted");
    context.font = "13px system-ui";
    context.fillText("No filled-trade equity points for this run.", 18, 82);
    return;
  }
  const values = points.map((point) => Number(point.equity));
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const range = Math.max(maximum - minimum, 1);
  context.strokeStyle = cssVar("--green");
  context.lineWidth = 2;
  context.beginPath();
  values.forEach((value, index) => {
    const x = 18 + (index / Math.max(values.length - 1, 1)) * (canvas.width - 36);
    const y = 18 + (1 - (value - minimum) / range) * (canvas.height - 36);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
}

function drawBarChart(canvasId, rows, options = {}) {
  const canvas = $(`#${canvasId}`);
  if (!canvas) return;
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (!rows.length) {
    context.fillStyle = cssVar("--muted");
    context.font = "13px system-ui";
    context.fillText("No data available.", 16, 92);
    return;
  }
  const left = 92;
  const right = 18;
  const top = 22;
  const rowHeight = 28;
  const maxValue = Math.max(...rows.map((row) => Math.abs(Number(row.value) || 0)), 1);
  context.font = "12px system-ui";
  rows.forEach((row, index) => {
    const value = Number(row.value) || 0;
    const y = top + index * rowHeight;
    const width = (Math.abs(value) / maxValue) * (canvas.width - left - right);
    context.fillStyle = cssVar("--muted");
    context.fillText(row.label, 12, y + 15);
    context.fillStyle = value < 0 ? cssVar("--red") : options.color || cssVar("--green");
    context.fillRect(left, y, Math.max(width, 2), 16);
    context.fillStyle = cssVar("--ink");
    context.fillText(formatNumber(value, options.digits ?? 2), left + width + 6, y + 13);
  });
}

function renderCandlestickChart(canvas, payload, highlightIndex = -1) {
  const candles = payload.candles || [];
  canvas.__candlePayload = payload;
  const context = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  if (!candles.length) {
    context.fillStyle = cssVar("--muted");
    context.font = "13px system-ui";
    context.fillText("No candles available for this dataset.", 18, 40);
    return;
  }
  const left = 58;
  const right = 16;
  const top = 14;
  const priceHeight = Math.floor((height - 64) * 0.74);
  const priceBottom = top + priceHeight;
  const volumeTop = priceBottom + 14;
  const volumeBottom = height - 28;
  let minPrice = Math.min(...candles.map((candle) => candle.low));
  let maxPrice = Math.max(...candles.map((candle) => candle.high));
  const pricePad = (maxPrice - minPrice || 1) * 0.05;
  minPrice -= pricePad;
  maxPrice += pricePad;
  const maxVolume = Math.max(...candles.map((candle) => candle.volume), 1);
  const count = candles.length;
  const slotWidth = (width - left - right) / count;
  const bodyWidth = Math.max(Math.min(slotWidth * 0.7, 12), 1);
  const xFor = (index) => left + slotWidth * index + slotWidth / 2;
  const yFor = (price) => top
    + (1 - (price - minPrice) / (maxPrice - minPrice)) * priceHeight;
  canvas.__layout = { left, slotWidth, count };
  context.strokeStyle = cssVar("--line");
  context.fillStyle = cssVar("--muted");
  context.font = "11px system-ui";
  context.lineWidth = 1;
  for (let tick = 0; tick <= 4; tick += 1) {
    const price = maxPrice - ((maxPrice - minPrice) * tick) / 4;
    const y = yFor(price);
    context.beginPath();
    context.moveTo(left, y);
    context.lineTo(width - right, y);
    context.stroke();
    context.fillText(formatNumber(price, price >= 1000 ? 0 : 2), 6, y + 4);
  }
  const timeTicks = Math.min(6, count);
  context.textAlign = "center";
  for (let tick = 0; tick < timeTicks; tick += 1) {
    const index = Math.floor((tick / Math.max(timeTicks - 1, 1)) * (count - 1));
    const label = String(candles[index].timestamp).replace("T", " ").slice(5, 16);
    context.fillText(label, xFor(index), height - 8);
  }
  context.textAlign = "left";
  const upColor = cssVar("--green");
  const downColor = cssVar("--red");
  candles.forEach((candle, index) => {
    const x = xFor(index);
    const color = candle.close >= candle.open ? upColor : downColor;
    context.strokeStyle = color;
    context.fillStyle = color;
    context.beginPath();
    context.moveTo(x, yFor(candle.high));
    context.lineTo(x, yFor(candle.low));
    context.stroke();
    const yOpen = yFor(candle.open);
    const yClose = yFor(candle.close);
    context.fillRect(
      x - bodyWidth / 2,
      Math.min(yOpen, yClose),
      bodyWidth,
      Math.max(Math.abs(yOpen - yClose), 1),
    );
    const volumeHeight = (candle.volume / maxVolume) * (volumeBottom - volumeTop);
    context.globalAlpha = 0.5;
    context.fillRect(
      x - bodyWidth / 2,
      volumeBottom - volumeHeight,
      bodyWidth,
      Math.max(volumeHeight, 1),
    );
    context.globalAlpha = 1;
  });
  if (highlightIndex >= 0 && highlightIndex < count) {
    context.strokeStyle = cssVar("--muted");
    context.setLineDash([4, 4]);
    context.beginPath();
    context.moveTo(xFor(highlightIndex), top);
    context.lineTo(xFor(highlightIndex), volumeBottom);
    context.stroke();
    context.setLineDash([]);
  }
}

function attachCandleInteractions(canvas, tooltip) {
  if (canvas.__candleWired) return;
  canvas.__candleWired = true;
  canvas.addEventListener("mousemove", (event) => {
    const payload = canvas.__candlePayload;
    const layout = canvas.__layout;
    if (!payload || !layout) return;
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left) * (canvas.width / rect.width);
    const index = Math.floor((x - layout.left) / layout.slotWidth);
    if (index < 0 || index >= layout.count) {
      tooltip.classList.add("hidden");
      renderCandlestickChart(canvas, payload);
      return;
    }
    renderCandlestickChart(canvas, payload, index);
    const candle = payload.candles[index];
    tooltip.classList.remove("hidden");
    tooltip.innerHTML = `
      <strong>${escapeHtml(String(candle.timestamp).replace("T", " "))}</strong>
      O ${formatNumber(candle.open)} · H ${formatNumber(candle.high)}
      · L ${formatNumber(candle.low)} · C ${formatNumber(candle.close)}
      · Vol ${formatNumber(candle.volume, 0)}`;
  });
  canvas.addEventListener("mouseleave", () => {
    tooltip.classList.add("hidden");
    if (canvas.__candlePayload) {
      renderCandlestickChart(canvas, canvas.__candlePayload);
    }
  });
}

function drawWorkflowChart(counts) {
  drawBarChart("workflow-chart", [
    { label: "Signals", value: counts.signals },
    { label: "Risk", value: counts.risk_decisions },
    { label: "Orders", value: counts.orders },
    { label: "Fills", value: counts.fills },
  ], { digits: 0, color: cssVar("--blue") });
}

function drawPnlChart(run) {
  drawBarChart("pnl-chart", [
    { label: "Net P&L", value: run.net_pnl },
    { label: "Drawdown", value: -Math.abs(Number(run.max_drawdown || 0)) },
    { label: "Trades", value: run.total_trades },
    { label: "Return %", value: run.return_pct },
  ], { digits: 2, color: cssVar("--green") });
}

function renderKnowledgeDocuments() {
  const container = $("#knowledge-doc-list");
  if (!container) return;
  // Show only user-facing documents; internal platform docs stay backend-only.
  state.knowledgeDocuments = state.knowledgeDocuments.filter(
    (doc) => doc.corpus !== "curated_project_docs",
  );
  if (!state.knowledgeDocuments.length) {
    container.innerHTML = `<div class="empty-state">No stored documents yet. Upload one above.</div>`;
    return;
  }
  container.innerHTML = state.knowledgeDocuments.map((doc) => `
    <div class="knowledge-doc-item">
      <div>
        <strong>${escapeHtml(doc.title)}</strong>
        <p>${escapeHtml(doc.document_type)} · ${formatNumber(doc.chunk_count, 0)} chunk(s) · ${escapeHtml(String(doc.ingested_at).slice(0, 16).replace("T", " "))}</p>
      </div>
      <button class="secondary-button analyze-doc-button" data-title="${escapeHtml(doc.title)}">Analyze in chat</button>
    </div>
  `).join("");
  container.querySelectorAll(".analyze-doc-button").forEach((button) => {
    button.addEventListener("click", () => {
      setView("workspace");
      const input = $("#chat-input");
      input.value = `Analyze document ${button.dataset.title}`;
      input.focus();
    });
  });
}

async function submitFundamentalsImport(event) {
  event.preventDefault();
  const button = $("#fundamentals-import-submit");
  const status = $("#fundamentals-status");
  button.disabled = true;
  try {
    const statements = JSON.parse($("#fundamentals-statements").value);
    if (!Array.isArray(statements)) {
      throw new Error("Statements must be a JSON array");
    }
    const symbol = $("#fundamentals-symbol").value.trim().toUpperCase();
    const payload = await api("/fundamentals/statements", {
      method: "POST",
      body: JSON.stringify({
        symbol,
        currency: $("#fundamentals-currency").value.trim(),
        source: $("#fundamentals-source").value.trim(),
        statements,
      }),
    });
    status.textContent = `Imported ${payload.imported_periods} period(s) for ${payload.symbol}. Ask the chat: "analyze ${payload.symbol} fundamentally".`;
    toast(`Statements imported for ${payload.symbol}`);
  } catch (error) {
    status.textContent = error.message;
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function submitKnowledgeUpload(event) {
  event.preventDefault();
  const button = $("#knowledge-upload-submit");
  const fileInput = $("#knowledge-upload-file");
  const title = $("#knowledge-upload-title").value.trim();
  let text = $("#knowledge-upload-text").value;
  button.disabled = true;
  try {
    const file = fileInput.files?.[0];
    if (file) {
      text = await file.text();
    }
    if (!text.trim()) {
      toast("Choose a .txt/.md file or paste the document text");
      return;
    }
    const documentType = file?.name?.toLowerCase().endsWith(".md")
      || file?.name?.toLowerCase().endsWith(".markdown")
      ? "markdown"
      : "text";
    const payload = await api("/knowledge/documents", {
      method: "POST",
      body: JSON.stringify({
        title,
        text,
        document_type: documentType,
      }),
    });
    toast(`Indexed "${title}" (${payload.chunk_count} chunk(s))`);
    $("#knowledge-upload-text").value = "";
    fileInput.value = "";
    const documents = await api("/knowledge/documents");
    state.knowledgeDocuments = documents.documents || [];
    renderKnowledgeDocuments();
    $("#metric-documents").textContent = formatNumber(state.knowledgeDocuments.length, 0);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

function renderDatasets() {
  const container = $("#dataset-list");
  if (!state.datasets.length) {
    container.innerHTML = `<div class="empty-state">No datasets yet.</div>`;
    return;
  }
  container.innerHTML = state.datasets.map((dataset) => {
    const chartable = dataset.storage_table === "market_ohlcv"
      || dataset.storage_table === "options_ohlcv";
    return `
    <article class="dataset-item" data-dataset-id="${escapeHtml(dataset.dataset_id)}">
      <div class="item-header">
        <div>
          <strong>${escapeHtml(dataset.symbol)} · ${escapeHtml(dataset.exchange)} · ${escapeHtml(dataset.interval)}</strong>
          <p>${formatNumber(dataset.row_count, 0)} candles · ${escapeHtml((dataset.start_ts || "").slice(0, 10))} to ${escapeHtml((dataset.end_ts || "").slice(0, 10))}</p>
        </div>
        <div class="section-actions">
          ${chartable ? '<button class="secondary-button chart-button">View chart</button>' : ""}
        </div>
      </div>
      <div class="chart-slot hidden">
        <canvas class="candle-canvas" width="960" height="380"></canvas>
        <div class="chart-tooltip hidden"></div>
      </div>
      <div class="freshness-slot"></div>
    </article>
  `;
  }).join("");
  container.querySelectorAll(".chart-button").forEach((button) => {
    button.addEventListener("click", () => toggleDatasetChart(button.closest("[data-dataset-id]"), button));
  });
}

async function toggleDatasetChart(article, button) {
  const slot = article.querySelector(".chart-slot");
  if (!slot.classList.contains("hidden")) {
    slot.classList.add("hidden");
    button.textContent = "View chart";
    return;
  }
  button.disabled = true;
  try {
    const datasetId = article.dataset.datasetId;
    const payload = await api(`/datasets/${encodeURIComponent(datasetId)}/ohlcv?limit=500`);
    slot.classList.remove("hidden");
    const canvas = slot.querySelector("canvas");
    renderCandlestickChart(canvas, payload);
    attachCandleInteractions(canvas, slot.querySelector(".chart-tooltip"));
    button.textContent = "Hide chart";
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function submitNlStrategyCompile(event) {
  event.preventDefault();
  const button = $("#compile-nl-strategy");
  const text = $("#nl-strategy-text").value.trim();
  if (!text) return;
  button.disabled = true;
  button.textContent = "Compiling...";
  try {
    const result = await api("/custom-strategy-specs/compile", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    state.nlCompiledResult = result;
    $("#nl-strategy-review").classList.remove("hidden");
    const spec = result.spec || {};
    const risk = spec.risk || {};
    const summaryParts = [
      `<strong>${escapeHtml(spec.symbol || "?")} ${escapeHtml(spec.timeframe || "?")} ${escapeHtml(spec.position_side || "long")}</strong>`,
    ];
    const entryRules = spec.entry_rules || [];
    const exitRules = spec.exit_rules || [];
    if (entryRules.length) {
      const entryText = entryRules.map((rule) => {
        const op = String(rule.operator || "").replaceAll("_", " ");
        return `${rule.left} ${op} ${rule.right}`;
      }).join(" AND ");
      summaryParts.push(`<div class="nl-detail">Entry: ${escapeHtml(entryText)}</div>`);
    }
    if (exitRules.length) {
      const exitText = exitRules.map((rule) => {
        const op = String(rule.operator || "").replaceAll("_", " ");
        return `${rule.left} ${op} ${rule.right}`;
      }).join(" AND ");
      summaryParts.push(`<div class="nl-detail">Exit: ${escapeHtml(exitText)}</div>`);
    }
    const riskParts = Object.entries(risk).map(([key, value]) => {
      if (key.endsWith("_pct")) return `${key.replace("_pct", "").replaceAll("_", " ")} ${(value * 100).toFixed(1)}%`;
      return `${key.replaceAll("_", " ")} ${value}`;
    });
    if (riskParts.length) summaryParts.push(`<div class="nl-detail">Risk: ${escapeHtml(riskParts.join(", "))}</div>`);
    if (result.unparsed_clauses?.length) {
      summaryParts.push(
        `<div class="nl-warning">Could not interpret: ${result.unparsed_clauses.map((clause) => `“${escapeHtml(clause)}”`).join(", ")}</div>`,
      );
    }
    for (const warning of result.warnings || []) {
      summaryParts.push(`<div class="nl-warning">${escapeHtml(warning)}</div>`);
    }
    const missing = result.missing_capabilities || [];
    if (missing.length) {
      summaryParts.push(
        `<div class="nl-warning">Blocking: ${missing.map((item) => escapeHtml(item.reason || item.value)).join("; ")}</div>`,
      );
    } else {
      summaryParts.push(`<div class="nl-ready">Executable by native rule runtime</div>`);
    }
    $("#nl-strategy-summary").innerHTML = summaryParts.join("");
    $("#nl-strategy-spec-json").value = JSON.stringify(spec, null, 2);
    $("#save-nl-strategy").disabled = missing.length > 0;
    $("#nl-strategy-validation").textContent = missing.length
      ? `${missing.length} blocking issue(s) must be resolved before saving.`
      : "Validation passed. Review the spec and save when ready.";
    $("#nl-strategy-validation").className = `nl-strategy-validation ${missing.length ? "attention" : "ready"}`;
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Compile for review";
  }
}

async function revalidateNlStrategy() {
  const button = $("#revalidate-nl-strategy");
  button.disabled = true;
  try {
    const spec = JSON.parse($("#nl-strategy-spec-json").value);
    const result = await api("/custom-strategy-specs/validate", {
      method: "POST",
      body: JSON.stringify({
        name: spec.name || `nl_${Date.now()}`,
        description: spec.description || "NL-compiled strategy",
        symbol: spec.symbol || "NIFTY",
        timeframe: spec.timeframe || "5m",
        indicators: spec.indicators || [],
        entry_rules: spec.entry_rules || [],
        exit_rules: spec.exit_rules || [],
        risk: spec.risk,
        position_side: spec.position_side || "long",
        feature_inputs: spec.feature_inputs || [],
        session: spec.session || null,
      }),
    });
    const missing = result.missing_capabilities || [];
    $("#save-nl-strategy").disabled = missing.length > 0;
    $("#nl-strategy-validation").textContent = missing.length
      ? `Requires review: ${missing.map((item) => item.value || item.kind).join(", ")}`
      : "Validation passed. Save when ready.";
    $("#nl-strategy-validation").className = `nl-strategy-validation ${missing.length ? "attention" : "ready"}`;
  } catch (error) {
    $("#nl-strategy-validation").textContent = error.message;
    $("#nl-strategy-validation").className = "nl-strategy-validation attention";
    $("#save-nl-strategy").disabled = true;
  } finally {
    button.disabled = false;
  }
}

async function saveNlStrategy() {
  const button = $("#save-nl-strategy");
  button.disabled = true;
  try {
    const spec = JSON.parse($("#nl-strategy-spec-json").value);
    const created = await api("/custom-strategy-specs", {
      method: "POST",
      body: JSON.stringify({
        name: spec.name || `nl_strategy_${Date.now()}`,
        description: spec.description || state.nlCompiledResult?.source_text || "NL-compiled strategy",
        symbol: spec.symbol || "NIFTY",
        timeframe: spec.timeframe || "5m",
        indicators: spec.indicators || [],
        entry_rules: spec.entry_rules || [],
        exit_rules: spec.exit_rules || [],
        risk: spec.risk,
        position_side: spec.position_side || "long",
        feature_inputs: spec.feature_inputs || [],
        session: spec.session || null,
      }),
    });
    state.customStrategySpecs = [
      created,
      ...state.customStrategySpecs,
    ].filter((item, index, items) => (
      items.findIndex((candidate) => candidate.spec_id === item.spec_id) === index
    ));
    renderCustomStrategyControls();
    const specSelect = $("#custom-strategy-spec-select");
    if (specSelect && created.spec_id) specSelect.value = created.spec_id;
    renderSelectedCustomStrategySpec();
    toast(`Strategy ${created.spec_id} saved`);
    $("#nl-strategy-review").classList.add("hidden");
    $("#nl-strategy-text").value = "";
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function loadSettings() {
  try {
    const config = await api("/settings");
    const grid = $("#settings-grid");
    const safetyNotice = $("#settings-safety-notice");
    const live = config.allow_live_trading;
    safetyNotice.textContent = live
      ? "Live trading is ENABLED by configuration. Paper orders still require explicit approval."
      : "Live trading is disabled. Paper orders use OpenAlgo analyzer mode and require explicit approval.";
    safetyNotice.className = `notice ${live ? "attention" : ""}`;
    const sensitiveKeys = new Set([
      "database_path", "artifacts_dir", "strategy_plugin_dir", "openalgo_root",
    ]);
    const entries = Object.entries(config).filter(([key]) => !sensitiveKeys.has(key));
    grid.innerHTML = entries.map(([key, value]) => {
      const display = typeof value === "object" ? JSON.stringify(value) : String(value);
      const tone = key === "allow_live_trading"
        ? (value ? "attention" : "ready")
        : (typeof value === "boolean"
          ? (value ? "ready" : "")
          : "");
      return `
        <div class="settings-item ${tone}">
          <span>${escapeHtml(key.replaceAll("_", " "))}</span>
          <strong>${escapeHtml(display)}</strong>
        </div>
      `;
    }).join("") || `<div class="empty-state">No configuration loaded.</div>`;
  } catch (error) {
    $("#settings-grid").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

function wireEvents() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      setView(button.dataset.view);
      if (button.dataset.view === "data") loadCoverage().catch(() => {});
      if (button.dataset.view === "agents") {
        loadAgents().catch(() => {});
        loadLeaderboard().catch(() => {});
        loadArenaSeasons().catch(() => {});
        loadContests().catch(() => {});
        loadSupervisorFindings().catch(() => {});
      }
    });
  });
  $("#chat-form").addEventListener("submit", submitChat);
  document.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      $("#chat-input").value = button.dataset.prompt;
      $("#chat-form").requestSubmit();
    });
  });
  $("#toggle-live-dashboard").addEventListener(
    "click",
    () => setAutoRefresh(!state.autoRefresh),
  );
  $("#backtest-form").addEventListener("submit", submitBacktest);
  $("#custom-strategy-form").addEventListener("submit", submitCustomStrategySpec);
  $("#validate-custom-strategy").addEventListener("click", validateCustomStrategySpec);
  $("#run-custom-strategy").addEventListener("click", runSelectedCustomStrategySpec);
  $("#custom-strategy-spec-select").addEventListener("change", renderSelectedCustomStrategySpec);
  $("#delete-custom-strategy").addEventListener("click", deleteCustomStrategySpec);
  $("#nl-strategy-form").addEventListener("submit", submitNlStrategyCompile);
  $("#revalidate-nl-strategy").addEventListener("click", revalidateNlStrategy);
  $("#save-nl-strategy").addEventListener("click", saveNlStrategy);
  $("#custom-strategy-template").addEventListener("change", () => {
    $("#custom-strategy-description").value = (
      `My ${$("#custom-strategy-template").selectedOptions[0].textContent} strategy.`
    );
    syncCustomStrategyRules();
  });
  syncCustomStrategyRules();
  $("#knowledge-upload-form").addEventListener("submit", submitKnowledgeUpload);
  $("#fundamentals-import-form").addEventListener("submit", submitFundamentalsImport);
  $("#login-form").addEventListener("submit", submitLogin);
  $("#logout-button").addEventListener("click", logout);
  $("#compare-runs").addEventListener("click", compareSelectedRuns);
  $("#experiment-form").addEventListener("submit", submitExperiment);
  document.querySelectorAll(".account-tab").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".account-tab").forEach((b) => b.classList.remove("active"));
      button.classList.add("active");
      loadAccountView(button.dataset.account).catch(() => {});
    });
  });
  $("#check-watches")?.addEventListener("click", checkWatches);
  $("#watches-list")?.addEventListener("click", (event) => {
    const button = event.target.closest(".watch-remove");
    if (button) removeWatch(button.dataset.watchId);
  });
  $("#refresh-agents")?.addEventListener("click", () => loadAgents().catch(() => {}));
  $("#refresh-leaderboard")?.addEventListener("click", () => loadLeaderboard().catch(() => {}));
  $("#arena-season-select")?.addEventListener("change", () => loadArenaStandings().catch(() => {}));
  $("#arena-tick")?.addEventListener("click", tickArena);
  $("#refresh-coverage")?.addEventListener("click", () => loadCoverage().catch(() => {}));
  $("#run-backfill")?.addEventListener("click", runBackfill);
  $("#supervisor-sweep")?.addEventListener("click", runSupervisorSweep);
  $("#supervisor-findings")?.addEventListener("click", (event) => {
    const button = event.target.closest(".finding-ack");
    if (button) acknowledgeFinding(button.dataset.findingId);
  });
  $("#refresh-contests")?.addEventListener("click", () => loadContests().catch(() => {}));
  $("#contests-list")?.addEventListener("click", (event) => {
    const button = event.target.closest(".contest-results");
    if (button) showContestResults(button.dataset.contestId);
  });
  $("#agents-list")?.addEventListener("click", (event) => {
    const button = event.target.closest(".agent-run");
    if (button) runAgent(button.dataset.agentId, button.dataset.agentName, button);
  });
  document.querySelectorAll(".emergency-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.emergencyAction;
      const label = action === "cancel_all_orders"
        ? "cancel ALL open orders"
        : "square off ALL open positions";
      const typed = window.prompt(
        `This will ${label} at the broker. Type CONFIRM to proceed.`,
      );
      if (typed !== "CONFIRM") {
        toast("Emergency action aborted");
        return;
      }
      button.disabled = true;
      try {
        const result = await api(
          `/openalgo/emergency/${encodeURIComponent(action)}`,
          { method: "POST" },
        );
        toast(`${label} requested (${result.record_id})`);
        await loadAccount();
        loadLiveTrades().catch(() => {});
      } catch (error) {
        toast(error.message);
      } finally {
        button.disabled = false;
      }
    });
  });
  $("#live-trades-refresh")?.addEventListener("click", () => {
    loadLiveTrades().catch(() => {});
  });
  $("#landing-news-refresh")?.addEventListener("click", () => {
    refreshLandingNews().catch(() => {});
  });
  $("#chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });
  $("#chat-input").addEventListener("input", (event) => {
    const input = event.target;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  });
  $("#theme-toggle").addEventListener("click", () => {
    const dark = !document.documentElement.classList.contains("dark-theme");
    applyTheme(dark);
    localStorage.setItem("iimc_theme", dark ? "dark" : "light");
    document.querySelectorAll("canvas.candle-canvas").forEach((canvas) => {
      if (canvas.__candlePayload) {
        renderCandlestickChart(canvas, canvas.__candlePayload);
      }
    });
  });
  $("#export-chat").addEventListener("click", exportChatMarkdown);
  document.addEventListener("keydown", (event) => {
    const activeTag = document.activeElement?.tagName;
    const inField = ["INPUT", "TEXTAREA", "SELECT"].includes(activeTag);
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      setView("workspace");
      $("#chat-input").focus();
      return;
    }
    if (inField) {
      if (event.key === "Escape") document.activeElement.blur();
      return;
    }
    if (event.key === "/") {
      event.preventDefault();
      setView("workspace");
      $("#chat-input").focus();
      return;
    }
    const viewKeys = {
      1: "workspace",
      2: "runs",
      3: "strategies",
      4: "data",
      5: "approvals",
      6: "monitor",
      7: "settings",
    };
    if (viewKeys[event.key] && !event.ctrlKey && !event.metaKey && !event.altKey) {
      setView(viewKeys[event.key]);
    }
  });
  $("#new-session").addEventListener("click", () => {
    state.sessionId = `session_ui_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
    localStorage.setItem("iimc_chat_session", state.sessionId);
    $("#messages").innerHTML = "";
    appendMessage("assistant", "New session started. What would you like to look at?");
  });
  const refreshActions = {
    "refresh-overview": loadOverview,
    "refresh-runs": async () => {
      const [runs, customSpecs] = await Promise.all([api("/runs?limit=50"), api("/custom-strategy-specs")]);
      state.runs = runs.runs || [];
      state.customStrategySpecs = customSpecs.custom_strategy_specs || [];
      renderRuns();
      renderBacktestControls();
    },
    "refresh-experiments": async () => {
      const experiments = await api("/experiments/robustness?limit=50");
      state.experiments = experiments.experiments || [];
      renderExperiments();
    },
    "refresh-data": async () => {
      const datasets = await api("/datasets");
      state.datasets = datasets.datasets || [];
      renderDatasets();
      renderBacktestControls();
    },
    "refresh-openalgo": loadAccount,
    "refresh-settings": loadSettings,
  };
  Object.entries(refreshActions).forEach(([id, action]) => {
    $(`#${id}`).addEventListener("click", async () => {
      const button = $(`#${id}`);
      button.disabled = true;
      try {
        await action();
        toast("Refreshed");
      } catch (error) {
        toast(error.message);
      } finally {
        button.disabled = false;
      }
    });
  });
}

async function start() {
  wireEvents();
  setAutoRefresh(state.autoRefresh, false);
  await loadHealth();
  if (state.health?.checks?.authentication_required && !state.principal) {
    return;
  }
  await restoreChatHistory();
  try {
    await loadOverview();
  } catch (error) {
    toast(error.message);
  }
}

start();
