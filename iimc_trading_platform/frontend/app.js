const state = {
  sessionId: `session_ui_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`,
  health: null,
  datasets: [],
  runs: [],
  strategies: [],
  experiments: [],
  portfolios: [],
  approvals: [],
  sandboxIntents: [],
  operations: null,
  jobs: [],
  tasks: [],
  backups: [],
  evaluations: [],
  retrievalEvaluations: [],
  retention: null,
  alerts: [],
  openalgoSnapshots: [],
  openalgoMonitor: null,
  marketNews: null,
  personas: [],
  customStrategySpecs: [],
  researchBriefs: [],
  executionReadiness: null,
  platformSummary: null,
  operatorReview: null,
  dashboardWidgets: JSON.parse(
    localStorage.getItem("iimc_dashboard_widgets")
    || '["research","assets","backtests","openalgo","risk","execution"]',
  ),
  autoRefresh: localStorage.getItem("iimc_auto_refresh") === "true",
  autoRefreshTimer: null,
  selectedRuns: new Set(),
  token: sessionStorage.getItem("iimc_access_token"),
  principal: null,
};

const $ = (selector) => document.querySelector(selector);
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
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...authHeaders,
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login") {
      showLogin();
    }
    const detail = payload.detail;
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
          <summary>Inspect stored evidence</summary>
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
    workspace: ["Trading Research Workspace", "Grounded analysis, audited tools, controlled execution."],
    operator: ["Operator Console", "Signal, risk, order, fill, and performance evidence in one place."],
    runs: ["Strategy Runs", "Deterministic execution and stored performance evidence."],
    experiments: ["Strategy Experiments", "Chronological out-of-sample validation and parameter stability."],
    portfolios: ["Portfolio Risk", "Durable positions, exposure reservations, and operator controls."],
    approvals: ["Human Approvals", "Explicit control over external sandbox actions."],
    data: ["Data Catalog", "Governed coverage, quality, provenance, and freshness."],
    openalgo: ["OpenAlgo Monitor", "Read-only analyzer, account state, and persisted reconciliation evidence."],
    operations: ["Platform Operations", "Readiness, scheduled maintenance, and failure visibility."],
  };
  document.querySelectorAll(".view").forEach((node) => node.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((node) => node.classList.remove("active"));
  $(`#view-${view}`).classList.add("active");
  $(`.nav-item[data-view="${view}"]`).classList.add("active");
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
        : "Live enabled";
      liveBadge.className = checks.live_trading_disabled
        ? "badge safe"
        : "badge attention";
    }
    $("#orchestrator-badge").textContent = llmConfigured
      ? `${llmProvider.toUpperCase()} orchestration`
      : "LLM key required";
    $("#verification-status").textContent = state.health.status === "healthy" ? "Healthy" : "Attention";
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
  $("#portfolio-create-form").classList.toggle(
    "hidden",
    !hasRole("researcher"),
  );
  $("#experiment-form").classList.toggle(
    "hidden",
    !hasRole("researcher"),
  );
  $("#create-backup").classList.toggle(
    "hidden",
    !hasRole("approver"),
  );
  $("#run-ai-eval").classList.toggle(
    "hidden",
    !hasRole("approver"),
  );
  $("#run-retrieval-eval").classList.toggle(
    "hidden",
    !hasRole("approver"),
  );
  $("#preview-retention").classList.toggle(
    "hidden",
    !hasRole("approver"),
  );
  $("#evaluate-alerts").classList.toggle(
    "hidden",
    !hasRole("approver"),
  );
  $("#generate-storage-plan").classList.toggle(
    "hidden",
    !hasRole("admin"),
  );
  $("#export-analytical").classList.toggle(
    "hidden",
    !hasRole("admin"),
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
    await loadOverview();
  } catch (error) {
    $("#auth-error").textContent = error.message;
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
  const canApprove = hasRole("approver");
  const [datasets, runs, strategies, customSpecs, experiments, portfolios, approvals, intents, documents, operations, platform, marketNews, personas, researchBriefs, executionReadiness, preferences] = await Promise.all([
    api("/datasets"),
    api("/runs?limit=50"),
    api("/strategies"),
    api("/custom-strategy-specs"),
    api("/experiments/robustness?limit=50"),
    api("/portfolios"),
    canApprove ? api("/approvals/pending") : Promise.resolve({ approvals: [] }),
    api("/sandbox/intents?limit=50"),
    api("/knowledge/documents"),
    api("/operations/summary"),
    api("/platform/summary"),
    api("/market-news/latest?limit=5"),
    api("/personas"),
    api("/platform/research/briefs?limit=5"),
    api("/platform/execution/readiness"),
    api("/platform/dashboard/preferences").catch(() => null),
  ]);
  applyDashboardPreferences(preferences);
  setAutoRefresh(state.autoRefresh, false);
  state.datasets = datasets.datasets || [];
  state.runs = runs.runs || [];
  state.strategies = strategies.strategies || [];
  state.customStrategySpecs = customSpecs.custom_strategy_specs || [];
  state.experiments = experiments.experiments || [];
  state.portfolios = portfolios.portfolios || [];
  state.approvals = approvals.approvals || [];
  state.sandboxIntents = intents.intents || [];
  state.operations = operations;
  state.platformSummary = platform;
  state.marketNews = marketNews;
  state.personas = personas.personas || [];
  state.researchBriefs = researchBriefs.briefs || [];
  state.executionReadiness = executionReadiness;
  const rowCount = state.datasets.reduce((sum, item) => sum + Number(item.row_count || 0), 0);
  $("#metric-rows").textContent = formatNumber(rowCount, 0);
  $("#metric-quality").textContent = state.datasets[0]?.quality?.status || "No dataset loaded";
  $("#metric-runs").textContent = formatNumber(state.runs.length, 0);
  $("#metric-approvals").textContent = formatNumber(state.approvals.length, 0);
  $("#metric-documents").textContent = formatNumber(documents.documents?.length || 0, 0);
  $("#approval-count").textContent = state.approvals.length;
  renderCommandCenter(documents.documents?.length || 0);
  renderReadinessResult(executionReadiness);
  renderPersonas();
  renderMarketNewsPanel();
  renderResearchBriefs();
  renderRuns();
  renderApprovals();
  renderPaperTrading();
  renderDatasets();
  renderExperiments();
  renderPortfolios();
  await loadOperatorConsole();
  await loadOpenAlgoHistory();
  await loadOperations();
}

function renderMarketNewsPanel(message = "") {
  const news = state.marketNews || {};
  const configured = Boolean(news.news_configured);
  const articles = news.articles || [];
  const statusText = message || (
    configured
      ? `${formatNumber(articles.length, 0)} stored provider-backed headline(s)`
      : "Market news provider is not configured; no fake headlines are generated."
  );
  $("#market-news-status").textContent = statusText;
  $("#market-news-status").className = `market-news-status ${configured ? "ready" : "attention"}`;
  $("#market-news-list").innerHTML = articles.length
    ? articles.map((article) => `
      <article class="market-news-item">
        <strong>${escapeHtml(article.title)}</strong>
        <span>${escapeHtml(article.source || "unknown")} · ${escapeHtml(article.published_at || article.retrieved_at || "stored")}</span>
      </article>
    `).join("")
    : `<div class="empty-state">No stored provider-backed headlines.</div>`;
}

function renderResearchBriefs(message = "") {
  const container = $("#research-brief-list");
  if (!container) return;
  const briefs = state.researchBriefs || [];
  const status = message
    ? `<div class="market-news-status ready">${escapeHtml(message)}</div>`
    : "";
  container.innerHTML = status + (briefs.length
    ? briefs.map((brief) => {
      const blocker = brief.next_blocker?.stage || "none";
      const dataset = brief.evidence?.dataset_id || "none";
      return `
        <article class="research-brief-item">
          <div>
            <strong>${escapeHtml(brief.title)}</strong>
            <span>${escapeHtml(brief.brief_id)} · dataset ${escapeHtml(dataset)} · blocker ${escapeHtml(blocker)}</span>
          </div>
          <details>
            <summary>Inspect brief</summary>
            <pre>${escapeHtml(JSON.stringify(brief, null, 2))}</pre>
          </details>
        </article>
      `;
    }).join("")
    : `<div class="empty-state">No stored research briefs yet.</div>`);
}

async function createResearchBrief() {
  const button = $("#create-research-brief");
  button.disabled = true;
  try {
    const payload = {
      symbol: $("#readiness-symbol").value.trim(),
      exchange: $("#readiness-exchange").value.trim(),
      asset_class: $("#readiness-asset").value,
      interval: $("#readiness-interval").value.trim(),
      start_date: $("#readiness-start").value.trim(),
      end_date: $("#readiness-end").value.trim(),
    };
    const brief = await api("/platform/research/briefs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.researchBriefs = [brief, ...state.researchBriefs]
      .filter((item, index, items) => (
        items.findIndex((candidate) => candidate.brief_id === item.brief_id) === index
      ))
      .slice(0, 5);
    renderResearchBriefs(`Stored ${brief.brief_id}`);
    toast(`Research brief ${brief.brief_id} stored`);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function submitMarketNews(event) {
  event.preventDefault();
  const button = $("#fetch-market-news");
  const params = new URLSearchParams({
    query: $("#market-news-query").value.trim(),
    symbol: $("#market-news-symbol").value.trim(),
  });
  button.disabled = true;
  $("#market-news-status").textContent = "Checking configured provider...";
  try {
    await api(`/market-news/fetch?${params.toString()}`, {
      method: "POST",
    });
    state.marketNews = await api("/market-news/latest?limit=5");
    renderMarketNewsPanel("Provider-backed headlines fetched and stored.");
  } catch (error) {
    state.marketNews = await api("/market-news/latest?limit=5");
    renderMarketNewsPanel(error.message);
  } finally {
    button.disabled = false;
  }
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
      "LLM orchestration",
      llmConfigured ? "configured" : "key required",
      `${llmProvider.toUpperCase()} routes natural-language requests into governed tools; final mode requires a configured model key.`,
      llmConfigured ? "ready" : "attention",
    ),
    capabilityStatus(
      "Market research",
      datasetCount ? "ready" : "needs data",
      `${formatNumber(datasetCount, 0)} governed dataset(s), ${formatNumber(documentCount, 0)} searchable document(s).`,
      datasetCount ? "ready" : "attention",
    ),
    capabilityStatus(
      "Backtesting",
      strategyCount ? "ready" : "needs strategies",
      `${formatNumber(strategyCount, 0)} registered strategy engine(s) with stored run evidence.`,
      strategyCount ? "ready" : "attention",
    ),
    capabilityStatus(
      "Paper trading",
      openalgoConfigured ? "credentialed" : "credential required",
      "OpenAlgo analyzer submission requires credentials, analyzer mode, and a risk-approved decision.",
      openalgoConfigured ? "gated" : "attention",
    ),
    capabilityStatus(
      "Live trading",
      liveEnabled ? "enabled" : "disabled",
      "Live execution is blocked unless explicitly configured and still follows backend risk gates.",
      liveEnabled ? "gated" : "blocked",
    ),
    capabilityStatus(
      "Strategy personas",
      personaCount ? "ready" : "needs profiles",
      `${formatNumber(personaCount, 0)} governed persona profile(s) available for strategy bias, risk rules, and dashboard focus.`,
      personaCount ? "ready" : "attention",
    ),
    capabilityStatus(
      "Multi-asset readiness",
      `${formatNumber(assetsWithData, 0)} with data`,
      "Equity, derivatives, commodity, and crypto requests are validated per symbol/provider before use.",
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
    ["openalgo", "OpenAlgo"],
    ["risk", "Risk"],
    ["execution", "Execution"],
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
  const executionPaths = platform.execution_paths || {};
  const gatedPaths = Object.values(executionPaths)
    .filter((item) => item.requires_human_approval).length;
  const widgetData = {
    research: ["Research data", formatNumber(state.datasets.reduce((sum, item) => sum + Number(item.row_count || 0), 0), 0), "Rows in governed local catalog"],
    assets: ["Asset coverage", `${formatNumber(assetsWithData, 0)} / ${formatNumber(assetsSupported, 0)}`, "Architectural support with local data availability tracked"],
    backtests: ["Backtests", formatNumber(state.runs.length, 0), latestRun ? `Latest ${latestRun.strategy || latestRun.strategy_id}` : "No completed run selected"],
    openalgo: ["OpenAlgo", state.openalgoMonitor?.status || "checking", state.openalgoMonitor?.message || "Provider status is loaded from backend"],
    risk: ["Approvals", formatNumber(state.approvals.length, 0), "Human-gated external actions"],
    execution: ["Execution gates", formatNumber(gatedPaths, 0), "Only configured live trading requires explicit approval"],
    news: ["Market news", state.platformSummary?.market_news?.configured ? "configured" : "not configured", "No provider means no fake headlines"],
    personas: ["Personas", formatNumber(state.personas.length, 0), "Governed strategy and risk profiles available to the assistant"],
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
      return `
        <article class="persona-item">
          <div>
            <strong>${escapeHtml(persona.name)}</strong>
            <span>${escapeHtml(persona.persona_id)}</span>
          </div>
          <p>${escapeHtml(persona.description)}</p>
          <small>Assets: ${escapeHtml(assets || "none")} · Bias: ${escapeHtml(strategies || "none")}</small>
        </article>
      `;
    }).join("")
    : `<div class="empty-state">No governed personas configured.</div>`;
}

function renderQuickActions() {
  const prompts = [
    "What governed market data is available?",
    "Check if RELIANCE NSE equity 5m is ready for research and execution.",
    "Can we paper trade NIFTY options, what is blocked?",
    "Explain the latest strategy run with signal, risk, order, fill, and performance evidence.",
    "What is the current OpenAlgo analyzer and account readiness?",
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

function stageTone(stage) {
  if (stage.can_start) return "ready";
  if (stage.status === "disabled") return "blocked";
  return "attention";
}

function renderExecutionStage(stage) {
  const blockers = stage.blockers?.length
    ? stage.blockers.join(", ")
    : "none";
  return `
    <article class="execution-stage ${stageTone(stage)}">
      <div class="stage-title">
        <strong>${escapeHtml(stage.stage.replaceAll("_", " "))}</strong>
        <span>${escapeHtml(stage.status)}</span>
      </div>
      <div class="stage-flags">
        <span>${stage.requires_human_approval ? "approval required" : "no manual approval"}</span>
        <span>${stage.external_side_effects ? "external action" : "internal only"}</span>
      </div>
      <small>Blockers: ${escapeHtml(blockers)}</small>
      <p>${escapeHtml(stage.next_action)}</p>
    </article>
  `;
}

function renderReadinessResult(payload, message = "") {
  const box = $("#readiness-result");
  if (!payload) {
    box.textContent = "Run a readiness check to see local data, provider status, and OpenAlgo path.";
    return;
  }
  const result = payload.data_readiness || payload.readiness || {};
  const stages = payload.stages || [];
  const blocker = payload.next_blocker;
  const status = result.verified_now
    ? "verified now"
    : result.local_dataset_exists
      ? "local data available"
      : result.unsupported_reason || "not verified";
  box.innerHTML = `
    <div class="readiness-head ${blocker ? "attention" : ""}">
      <strong>${escapeHtml(result.symbol || payload.symbol)} ${escapeHtml(result.exchange || payload.exchange)}</strong>
      <span>${escapeHtml(message || status)}</span>
    </div>
    <div class="readiness-grid">
      <div><span>Architecture</span><strong>${result.supported_by_architecture ? "supported" : "unsupported"}</strong></div>
      <div><span>Local data</span><strong>${result.local_dataset_exists ? `${formatNumber(result.rows_available, 0)} rows` : "none"}</strong></div>
      <div><span>Provider</span><strong>${result.provider_configured ? "configured" : "not configured"}</strong></div>
      <div><span>OpenAlgo</span><strong>${escapeHtml(result.analyzer_path_status || payload.openalgo?.status || "unknown")}</strong></div>
      <div><span>Live path</span><strong>${escapeHtml(result.live_path_status || "unknown")}</strong></div>
      <div><span>Fallback</span><strong>${payload.no_synthetic_fallback ? "no synthetic data" : "attention"}</strong></div>
    </div>
    <div class="execution-stage-list">
      ${stages.map(renderExecutionStage).join("")}
    </div>
    ${blocker ? `
      <div class="readiness-blocker">
        <strong>Next blocker: ${escapeHtml(blocker.stage)}</strong>
        <span>${escapeHtml(blocker.next_action)}</span>
      </div>
    ` : ""}
  `;
}

async function submitReadiness(event) {
  event.preventDefault();
  const button = $("#run-readiness");
  const box = $("#readiness-result");
  button.disabled = true;
  box.textContent = "Checking readiness...";
  const params = new URLSearchParams({
    symbol: $("#readiness-symbol").value.trim(),
    exchange: $("#readiness-exchange").value.trim(),
    asset_class: $("#readiness-asset").value,
    interval: $("#readiness-interval").value.trim(),
    start_date: $("#readiness-start").value.trim(),
    end_date: $("#readiness-end").value.trim(),
  });
  try {
    const readiness = await api(`/platform/execution/readiness?${params.toString()}`);
    state.executionReadiness = readiness;
    renderReadinessResult(readiness);
  } catch (error) {
    box.innerHTML = `<div class="readiness-head attention"><strong>Safe failure</strong><span>${escapeHtml(error.message)}</span></div>`;
  } finally {
    button.disabled = false;
  }
}

async function loadOpenAlgoHistory() {
  const [payload, monitor] = await Promise.all([
    api("/openalgo/snapshots?limit=50"),
    api("/platform/openalgo/monitor"),
  ]);
  state.openalgoMonitor = monitor;
  state.openalgoSnapshots = payload.snapshots || [];
  const configured = Boolean(payload.configured);
  $("#openalgo-notice").textContent = configured
    ? `${monitor.message} Captures are read-only; paper orders use analyzer mode and risk gates.`
    : "OpenAlgo credentials are not configured yet. Stored history remains visible, and capture controls activate when the key is supplied.";
  document.querySelectorAll(".snapshot-button").forEach((button) => {
    button.disabled = !configured;
  });
  renderOpenAlgoMonitor(monitor);
  const table = $("#openalgo-snapshots-table");
  table.innerHTML = state.openalgoSnapshots.length
    ? state.openalgoSnapshots.map((snapshot) => `
      <tr data-snapshot-id="${escapeHtml(snapshot.snapshot_id)}">
        <td>${escapeHtml(snapshot.captured_at)}</td>
        <td>${escapeHtml(snapshot.snapshot_type)}</td>
        <td>${escapeHtml(snapshot.snapshot_id)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="3">No OpenAlgo snapshots have been captured.</td></tr>`;
  table.querySelectorAll("[data-snapshot-id]").forEach((row) => {
    row.addEventListener("click", () => {
      const snapshot = state.openalgoSnapshots.find(
        (item) => item.snapshot_id === row.dataset.snapshotId,
      );
      if (snapshot) renderOpenAlgoSnapshot(snapshot);
    });
  });
  renderDashboardWidgets(state.platformSummary?.latest_completed_run || state.runs[0]);
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

async function loadOperatorConsole() {
  const payload = await api("/platform/operator-review");
  state.operatorReview = payload;
  const run = payload.latest_completed_run;
  $("#operator-goal").textContent = payload.operator_goal;
  $("#operator-run-id").textContent = run?.run_id || "-";
  $("#operator-run-status").textContent = run
    ? `${run.strategy} on ${run.dataset_id}`
    : "Run a research backtest to populate this panel.";
  $("#operator-net-pnl").textContent = run ? formatNumber(run.net_pnl) : "-";
  $("#operator-trades").textContent = run ? formatNumber(run.total_trades, 0) : "-";
  $("#operator-drawdown").textContent = run ? formatNumber(run.max_drawdown) : "-";

  const storageEntries = Object.entries(payload.run_storage_evidence || {});
  $("#operator-storage-table").innerHTML = storageEntries.length
    ? storageEntries.map(([tableName, count]) => `
      <tr>
        <td>${escapeHtml(tableName)}</td>
        <td>${formatNumber(count, 0)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="2">No completed run storage evidence yet.</td></tr>`;

  $("#operator-workflow").innerHTML = payload.workflow.map((step, index) => `
    <article class="workflow-step">
      <strong>${formatNumber(index + 1, 0)}. ${escapeHtml(step.stage.replaceAll("_", " "))}</strong>
      <p>${escapeHtml(step.purpose)}</p>
      <small>${escapeHtml(step.stored_in.join(", "))}</small>
    </article>
  `).join("");

  $("#operator-actions").innerHTML = payload.ui_actions.map((action) => `
    <button class="secondary-button operator-action" data-target-view="${escapeHtml(action.target_view)}">
      ${escapeHtml(action.label)}
    </button>
  `).join("");
  $("#operator-actions").querySelectorAll(".operator-action").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.targetView));
  });
}

function renderOpenAlgoSnapshot(snapshot) {
  $("#snapshot-current-label").textContent = `${snapshot.snapshot_type} · ${snapshot.snapshot_id} · ${snapshot.captured_at}`;
  $("#snapshot-current-data").textContent = JSON.stringify(
    snapshot.data,
    null,
    2,
  );
}

async function captureOpenAlgoSnapshot(snapshotType) {
  const button = document.querySelector(`[data-snapshot-type="${snapshotType}"]`);
  const originalLabel = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "Loading...";
  }
  try {
    const snapshot = await api(
      `/openalgo/${encodeURIComponent(snapshotType)}`,
    );
    renderOpenAlgoSnapshot(snapshot);
    toast(`${snapshotType} snapshot captured`);
    await loadOpenAlgoHistory();
  } catch (error) {
    $("#snapshot-current-label").textContent = `${snapshotType} capture failed safely`;
    $("#snapshot-current-data").textContent = JSON.stringify(
      { ok: false, safe_failure: true, message: error.message },
      null,
      2,
    );
    toast(error.message);
  } finally {
    if (button) {
      button.disabled = !state.openalgoMonitor?.configured;
      button.textContent = originalLabel;
    }
  }
}

function renderPortfolios() {
  const table = $("#portfolios-table");
  table.innerHTML = state.portfolios.length
    ? state.portfolios.map((portfolio) => `
      <tr data-portfolio-id="${escapeHtml(portfolio.portfolio_id)}">
        <td>${escapeHtml(portfolio.portfolio_id)}</td>
        <td>${escapeHtml(portfolio.name)}</td>
        <td>${formatNumber(portfolio.cash_balance)}</td>
        <td><span class="status-pill">${escapeHtml(portfolio.status)}</span></td>
        <td>${escapeHtml(portfolio.updated_at)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="5">No portfolios are stored.</td></tr>`;
  table.querySelectorAll("tr[data-portfolio-id]").forEach((row) => {
    row.addEventListener("click", () => loadPortfolio(row.dataset.portfolioId));
  });
}

async function submitPortfolio(event) {
  event.preventDefault();
  try {
    const portfolio = await api("/portfolios", {
      method: "POST",
      body: JSON.stringify({
        name: $("#portfolio-name").value,
        starting_cash: Number($("#portfolio-cash").value),
        base_currency: "INR",
      }),
    });
    toast(`Portfolio ${portfolio.portfolio_id} created`);
    await loadOverview();
    await loadPortfolio(portfolio.portfolio_id);
  } catch (error) {
    toast(error.message);
  }
}

async function loadPortfolio(portfolioId) {
  try {
    const portfolio = await api(`/portfolios/${encodeURIComponent(portfolioId)}`);
    const detail = $("#portfolio-detail");
    detail.classList.remove("hidden");
    detail.innerHTML = `
      <div class="section-heading">
        <div><h2>${escapeHtml(portfolio.name)}</h2><p>${escapeHtml(portfolio.portfolio_id)}</p></div>
        <div class="section-actions">
          <span class="status-pill">${portfolio.risk_control.trading_enabled ? "trading enabled" : "kill switch active"}</span>
          ${hasRole("approver") ? `
            <button class="secondary-button" id="portfolio-disable">${portfolio.risk_control.trading_enabled ? "Stop trading" : "Keep stopped"}</button>
            <button class="secondary-button" id="portfolio-enable">Enable trading</button>
          ` : ""}
        </div>
      </div>
      <div class="detail-grid">
        <div class="detail-cell"><span>Cash</span><strong>${formatNumber(portfolio.cash_balance)}</strong></div>
        <div class="detail-cell"><span>Equity</span><strong>${formatNumber(portfolio.equity)}</strong></div>
        <div class="detail-cell"><span>Gross exposure</span><strong>${formatNumber(portfolio.gross_exposure)}</strong></div>
        <div class="detail-cell"><span>Reserved notional</span><strong>${formatNumber(portfolio.reserved_notional)}</strong></div>
      </div>
      ${hasRole("researcher") ? `
        <form class="portfolio-risk-form" id="portfolio-risk-form">
          <label><span>Symbol</span><input id="portfolio-symbol" value="NIFTY" required></label>
          <label><span>Side</span><select id="portfolio-side"><option>BUY</option><option>SELL</option></select></label>
          <label><span>Quantity</span><input id="portfolio-quantity" type="number" min="1" value="1" required></label>
          <label><span>Price</span><input id="portfolio-price" type="number" min="0.01" step="0.01" required></label>
          <button type="submit" class="primary-button">Check and reserve risk</button>
        </form>
        <div id="portfolio-risk-result"></div>
      ` : ""}
      <div class="table-wrap portfolio-positions">
        <table>
          <thead><tr><th>Symbol</th><th>Quantity</th><th>Average</th><th>Last</th><th>Market value</th><th>Realized P&amp;L</th></tr></thead>
          <tbody>${portfolio.positions.length ? portfolio.positions.map((position) => `
            <tr>
              <td>${escapeHtml(position.symbol)}</td>
              <td>${formatNumber(position.quantity, 0)}</td>
              <td>${formatNumber(position.average_price)}</td>
              <td>${formatNumber(position.last_price)}</td>
              <td>${formatNumber(position.market_value)}</td>
              <td>${formatNumber(position.realized_pnl)}</td>
            </tr>
          `).join("") : `<tr><td colspan="6">No open positions.</td></tr>`}</tbody>
        </table>
      </div>
    `;
    $("#portfolio-risk-form")?.addEventListener("submit", (event) => (
      submitPortfolioRisk(event, portfolioId)
    ));
    $("#portfolio-disable")?.addEventListener("click", () => (
      setPortfolioControl(portfolioId, false)
    ));
    $("#portfolio-enable")?.addEventListener("click", () => (
      setPortfolioControl(portfolioId, true)
    ));
  } catch (error) {
    toast(error.message);
  }
}

async function submitPortfolioRisk(event, portfolioId) {
  event.preventDefault();
  try {
    const price = Number($("#portfolio-price").value);
    const result = await api(
      `/portfolios/${encodeURIComponent(portfolioId)}/risk-check`,
      {
        method: "POST",
        body: JSON.stringify({
          symbol: $("#portfolio-symbol").value.toUpperCase(),
          side: $("#portfolio-side").value,
          quantity: Number($("#portfolio-quantity").value),
          price,
        }),
      },
    );
    const container = $("#portfolio-risk-result");
    container.className = `risk-result ${result.approved ? "approved" : "rejected"}`;
    container.innerHTML = `
      <strong>${result.approved ? "Approved" : "Rejected"}</strong>
      <span>${escapeHtml(result.reason)}</span>
      ${result.reservation_id ? `
        <div class="section-actions">
          ${hasRole("approver") ? `<button class="secondary-button" id="consume-reservation">Record fill</button>` : ""}
          <button class="secondary-button" id="release-reservation">Release reservation</button>
        </div>
      ` : ""}
    `;
    $("#consume-reservation")?.addEventListener("click", async () => {
      try {
        await api(`/portfolios/${encodeURIComponent(portfolioId)}/fills`, {
          method: "POST",
          body: JSON.stringify({
            reservation_id: result.reservation_id,
            reference_id: `ui_fill_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`,
            price,
            fees: 0,
          }),
        });
        toast("Portfolio fill recorded");
        await loadOverview();
        await loadPortfolio(portfolioId);
      } catch (error) {
        toast(error.message);
      }
    });
    $("#release-reservation")?.addEventListener("click", async () => {
      try {
        await api(
          `/portfolio-risk/reservations/${encodeURIComponent(result.reservation_id)}/release`,
          { method: "POST" },
        );
        toast("Reservation released");
        await loadOverview();
        await loadPortfolio(portfolioId);
      } catch (error) {
        toast(error.message);
      }
    });
  } catch (error) {
    toast(error.message);
  }
}

async function setPortfolioControl(portfolioId, enabled) {
  const reason = window.prompt(
    enabled ? "Reason for enabling trading" : "Reason for stopping trading",
  );
  if (!reason) return;
  try {
    await api(
      `/portfolios/${encodeURIComponent(portfolioId)}/trading-control`,
      {
        method: "POST",
        body: JSON.stringify({ enabled, reason }),
      },
    );
    toast(enabled ? "Trading enabled" : "Kill switch activated");
    await loadOverview();
    await loadPortfolio(portfolioId);
  } catch (error) {
    toast(error.message);
  }
}

function renderExperiments() {
  const strategySelect = $("#experiment-strategy");
  const datasetSelect = $("#experiment-dataset");
  strategySelect.innerHTML = state.strategies.map((strategy) => `
    <option value="${escapeHtml(strategy.name)}">${escapeHtml(strategy.name)}</option>
  `).join("");
  datasetSelect.innerHTML = marketDatasets().map((dataset) => `
    <option value="${escapeHtml(dataset.dataset_id)}">${escapeHtml(dataset.dataset_id)}</option>
  `).join("");
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
  datasetSelect.innerHTML = marketDatasets().map((dataset) => `
    <option value="${escapeHtml(dataset.dataset_id)}">${escapeHtml(dataset.dataset_id)}</option>
  `).join("");
  strategySelect.innerHTML = state.strategies.map((strategy) => `
    <option value="${escapeHtml(strategy.name)}">${escapeHtml(strategy.name)}</option>
  `).join("");
  renderBacktestParameters(strategySelect.value);
  strategySelect.onchange = () => renderBacktestParameters(strategySelect.value);
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
  datasetSelect.innerHTML = marketDatasets().map((dataset) => `
    <option value="${escapeHtml(dataset.dataset_id)}">${escapeHtml(dataset.dataset_id)}</option>
  `).join("");
  if (currentDataset) datasetSelect.value = currentDataset;

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
      : "Rules are executable by the native deterministic runtime.";
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
      const step = meta.type === "integer" ? "1" : "0.001";
      return `
        <label>
          <span>${escapeHtml(name.replaceAll("_", " "))}</span>
          <input class="backtest-parameter"
            data-parameter-name="${escapeHtml(name)}"
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

function appendMessage(role, content, className = "") {
  const article = document.createElement("article");
  article.className = `message ${role} ${className}`.trim();
  article.innerHTML = `
    <div class="message-role">${role === "user" ? "You" : "Assistant"}</div>
    <p>${escapeHtml(content)}</p>
  `;
  $("#messages").appendChild(article);
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function renderEvidence(payload) {
  $("#evidence-empty").classList.add("hidden");
  const container = $("#evidence-content");
  container.classList.remove("hidden");
  const ids = payload.evaluation?.evidence_ids || [];
  container.innerHTML = `
    <div class="evidence-item">
      <span>Intent</span><strong>${escapeHtml(payload.intent)}</strong>
    </div>
    <div class="evidence-item">
      <span>Orchestration</span><strong>${escapeHtml(payload.orchestration_mode)}</strong>
    </div>
    <div class="evidence-item">
      <span>Evaluator</span><strong>${payload.evaluation?.passed ? "Passed" : "Attention required"}</strong>
    </div>
    <div class="evidence-item">
      <span>Evidence IDs</span><strong>${ids.map(escapeHtml).join("<br>") || "None"}</strong>
    </div>
    <pre class="json-view">${escapeHtml(JSON.stringify(payload.data, null, 2))}</pre>
  `;
}

async function submitChat(event) {
  event.preventDefault();
  const input = $("#chat-input");
  const message = input.value.trim();
  if (!message) return;
  appendMessage("user", message);
  input.value = "";
  $("#send-button").disabled = true;
  try {
    const payload = await api("/chat", {
      method: "POST",
      body: JSON.stringify({ session_id: state.sessionId, message }),
    });
    appendMessage("assistant", payload.answer);
    renderEvidence(payload);
  } catch (error) {
    appendMessage("assistant", error.message, "error");
  } finally {
    $("#send-button").disabled = false;
    input.focus();
  }
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
      [...document.querySelectorAll(".backtest-parameter")].map((input) => [
        input.dataset.parameterName,
        Number(input.value),
      ]),
    );
    const payload = await api("/platform/backtest/run", {
      method: "POST",
      body: JSON.stringify({
        dataset_id: $("#backtest-dataset").value,
        strategy_name: strategyName,
        execution_mode: $("#backtest-mode").value,
        parameters,
        slippage_bps: Number($("#backtest-slippage").value),
      }),
    });
    resultBox.textContent = JSON.stringify({
      ...payload,
      data_source: "real",
      visible_in_openalgo: false,
      explanation: payload.execution_mode === "semi_auto"
        ? "This semi-auto run creates risk-approved paper order evidence; OpenAlgo analyzer submission uses an explicit intent."
        : "This is an IIMC historical backtest, not OpenAlgo broker activity.",
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
    button.textContent = "Run IIMC backtest";
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
            <p>Persisted summary from the research ledger, not recalculated in the browser.</p>
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
          <div><strong>Workflow Counts</strong><span>Persisted evidence records</span></div>
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
            <p>Chronological persisted evidence. Parent IDs connect each decision to its upstream event.</p>
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
    $("#generate-report")?.addEventListener("click", () => generateReport(runId));
    $("#show-all-events")?.addEventListener("click", (event) => {
      $("#timeline-events").innerHTML = renderTimelineRows(timeline.events);
      event.currentTarget.remove();
    });
  } catch (error) {
    toast(error.message);
  }
}

async function submitKnowledgeSearch(event) {
  event.preventDefault();
  const button = $("#run-knowledge-search");
  const box = $("#knowledge-search-result");
  button.disabled = true;
  button.textContent = "Searching...";
  try {
    const payload = await api("/knowledge/search", {
      method: "POST",
      body: JSON.stringify({
        query: $("#knowledge-query").value,
        limit: 5,
      }),
    });
    box.textContent = JSON.stringify(payload, null, 2);
  } catch (error) {
    box.textContent = JSON.stringify(
      { ok: false, safe_failure: true, message: error.message },
      null,
      2,
    );
  } finally {
    button.disabled = false;
    button.textContent = "Search docs";
  }
}

async function generateReport(runId) {
  try {
    const report = await api(`/runs/${encodeURIComponent(runId)}/reports`, {
      method: "POST",
    });
    toast(`Report ${report.report_id} generated`);
  } catch (error) {
    toast(error.message);
  }
}

async function compareSelectedRuns() {
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
  }
}

async function loadOperations() {
  if (!state.operations) state.operations = await api("/operations/summary");
  $("#ops-enabled").textContent = formatNumber(state.operations.enabled_jobs, 0);
  $("#ops-failed").textContent = formatNumber(state.operations.failed_job_runs, 0);
  $("#ops-stale").textContent = formatNumber(state.operations.stale_assessments, 0);
  $("#ops-uncertain").textContent = formatNumber(state.operations.uncertain_submissions, 0);
  $("#ops-active-tasks").textContent = formatNumber(state.operations.active_tasks, 0);
  $("#ops-failed-tasks").textContent = formatNumber(state.operations.failed_tasks, 0);
  $("#ops-critical-alerts").textContent = formatNumber(
    state.operations.active_critical_alerts,
    0,
  );
  $("#ops-warning-alerts").textContent = formatNumber(
    state.operations.active_warning_alerts,
    0,
  );
  const taskPayload = await api("/tasks?limit=25");
  state.tasks = taskPayload.tasks || [];
  const evaluationPayload = await api("/evaluations?limit=25");
  state.evaluations = evaluationPayload.evaluations || [];
  const retrievalEvaluationPayload = await api(
    "/evaluations/retrieval?limit=25",
  );
  state.retrievalEvaluations = (
    retrievalEvaluationPayload.evaluations || []
  );
  if (hasRole("approver")) {
    const [
      jobPayload,
      backupPayload,
      retentionPayload,
      alertPayload,
    ] = await Promise.all([
      api("/jobs"),
      api("/operations/backups"),
      api("/operations/retention"),
      api("/operations/alerts"),
    ]);
    state.jobs = jobPayload.jobs || [];
    state.backups = backupPayload.backups || [];
    state.retention = retentionPayload;
    state.alerts = alertPayload.alerts || [];
  } else {
    state.jobs = [];
    state.backups = [];
    state.retention = null;
    state.alerts = [];
  }
  const table = $("#jobs-table");
  table.innerHTML = state.jobs.length
    ? state.jobs.map((job) => `
      <tr>
        <td>${escapeHtml(job.name)}</td>
        <td>${escapeHtml(job.job_type)}</td>
        <td>${job.enabled ? "enabled" : "disabled"}</td>
        <td>${escapeHtml(job.next_run_at)}</td>
        <td>${escapeHtml(job.last_status || "never run")}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="5">${hasRole("approver") ? "No scheduled jobs." : "Job details require approver access."}</td></tr>`;
  const taskTable = $("#tasks-table");
  taskTable.innerHTML = state.tasks.length
    ? state.tasks.map((task) => `
      <tr>
        <td>${escapeHtml(task.task_id)}</td>
        <td>${escapeHtml(task.task_type)}</td>
        <td>${escapeHtml(task.status)}</td>
        <td>${formatNumber(task.attempt, 0)} / ${formatNumber(task.max_attempts, 0)}</td>
        <td>${escapeHtml(task.error_type || task.result?.experiment_id || "-")}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="5">No durable tasks.</td></tr>`;
  const backupTable = $("#backups-table");
  backupTable.innerHTML = state.backups.length
    ? state.backups.map((backup) => `
      <tr>
        <td>${escapeHtml(backup.backup_id)}</td>
        <td>${escapeHtml(backup.created_at || "-")}</td>
        <td>${formatNumber(backup.archive_size_bytes, 0)}</td>
        <td>${formatNumber(Object.keys(backup.table_counts || {}).length, 0)}</td>
        <td>${escapeHtml(backup.status)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="5">${hasRole("approver") ? "No backups created." : "Backup details require approver access."}</td></tr>`;
  const evaluationTable = $("#evaluations-table");
  evaluationTable.innerHTML = state.evaluations.length
    ? state.evaluations.map((evaluation) => `
      <tr>
        <td>${escapeHtml(evaluation.eval_run_id)}</td>
        <td>${escapeHtml(evaluation.orchestration_mode)}</td>
        <td>${formatNumber(evaluation.passed_count, 0)} / ${formatNumber(evaluation.case_count, 0)}</td>
        <td>${formatNumber(Number(evaluation.pass_rate) * 100, 1)}%</td>
        <td>${escapeHtml(evaluation.status)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="5">No AI evaluation runs.</td></tr>`;
  const retrievalEvaluationTable = $("#retrieval-evaluations-table");
  retrievalEvaluationTable.innerHTML = state.retrievalEvaluations.length
    ? state.retrievalEvaluations.map((evaluation) => `
      <tr>
        <td>${escapeHtml(evaluation.eval_run_id)}</td>
        <td>${escapeHtml(evaluation.retrieval_method)}</td>
        <td>${formatNumber(Number(evaluation.recall_at_k) * 100, 1)}%</td>
        <td>${formatNumber(evaluation.mean_reciprocal_rank, 4)}</td>
        <td>${formatNumber(evaluation.ndcg_at_k, 4)}</td>
        <td>${escapeHtml(evaluation.status)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="6">No retrieval evaluation runs.</td></tr>`;
  const retentionTable = $("#retention-table");
  const latestRetention = state.retention?.runs?.[0];
  retentionTable.innerHTML = state.retention?.policies?.length
    ? state.retention.policies.map((policy) => {
      const counts = latestRetention?.candidate_counts?.[policy.policy_name] || {};
      const candidates = Object.values(counts).reduce(
        (total, value) => total + Number(value || 0),
        0,
      );
      return `
        <tr>
          <td>${escapeHtml(policy.policy_name)}</td>
          <td>${formatNumber(policy.retention_days, 0)}</td>
          <td>${policy.enabled ? "enabled" : "disabled"}</td>
          <td>${policy.automatic_execution ? "automatic" : "manual only"}</td>
          <td>${formatNumber(candidates, 0)}</td>
        </tr>
      `;
    }).join("")
    : `<tr><td colspan="5">${hasRole("approver") ? "No retention policies." : "Retention details require approver access."}</td></tr>`;
  const alertsTable = $("#alerts-table");
  alertsTable.innerHTML = state.alerts.length
    ? state.alerts.map((alert) => `
      <tr>
        <td>${escapeHtml(alert.severity)}</td>
        <td>${escapeHtml(alert.rule_key)}</td>
        <td>${escapeHtml(alert.status)}</td>
        <td>${escapeHtml(alert.message)}</td>
        <td>${escapeHtml(alert.runbook_uri)}</td>
        <td>
          ${alert.status === "active" ? `<button class="secondary-button acknowledge-alert" data-alert-id="${escapeHtml(alert.alert_id)}">Acknowledge</button>` : "-"}
        </td>
      </tr>
    `).join("")
    : `<tr><td colspan="6">${hasRole("approver") ? "No active operational alerts." : "Alert details require approver access."}</td></tr>`;
  alertsTable.querySelectorAll(".acknowledge-alert").forEach((button) => {
    button.addEventListener("click", () => acknowledgeAlert(button.dataset.alertId));
  });
}

async function createBackup() {
  const button = $("#create-backup");
  button.disabled = true;
  try {
    const backup = await api("/operations/backups", { method: "POST" });
    toast(`Backup ${backup.backup_id} created and verified`);
    state.operations = null;
    await loadOperations();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function runAiEvaluation() {
  const button = $("#run-ai-eval");
  button.disabled = true;
  try {
    const evaluation = await api("/evaluations/run", {
      method: "POST",
      body: JSON.stringify({ mode: "offline" }),
    });
    toast(
      `Evaluation ${evaluation.eval_run_id}: ${formatNumber(evaluation.pass_rate * 100, 1)}%`,
    );
    await loadOperations();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function runRetrievalEvaluation() {
  const button = $("#run-retrieval-eval");
  button.disabled = true;
  try {
    const evaluation = await api("/evaluations/retrieval/run", {
      method: "POST",
    });
    toast(
      `Retrieval ${evaluation.eval_run_id}: MRR ${formatNumber(evaluation.mean_reciprocal_rank, 4)}`,
    );
    await loadOperations();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function previewRetention() {
  const button = $("#preview-retention");
  button.disabled = true;
  try {
    const preview = await api("/operations/retention/preview", {
      method: "POST",
      body: JSON.stringify({ policy_names: null }),
    });
    const candidates = Object.values(preview.candidate_counts).reduce(
      (total, counts) => (
        total
        + Object.values(counts).reduce(
          (subtotal, value) => subtotal + Number(value || 0),
          0,
        )
      ),
      0,
    );
    toast(`Retention preview found ${candidates} expired records`);
    await loadOperations();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function evaluateAlerts() {
  const button = $("#evaluate-alerts");
  button.disabled = true;
  try {
    const evaluation = await api("/operations/alerts/evaluate", {
      method: "POST",
    });
    toast(
      `Alerts evaluated: ${evaluation.active_count} active, ${evaluation.acknowledged_count} acknowledged`,
    );
    state.operations = null;
    await loadOverview();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function acknowledgeAlert(alertId) {
  const reason = window.prompt("Acknowledgement reason");
  if (!reason || reason.trim().length < 3) return;
  try {
    await api(
      `/operations/alerts/${encodeURIComponent(alertId)}/acknowledge`,
      {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim() }),
      },
    );
    toast(`Alert ${alertId} acknowledged`);
    state.operations = null;
    await loadOverview();
  } catch (error) {
    toast(error.message);
  }
}

async function generateStoragePlan() {
  const button = $("#generate-storage-plan");
  button.disabled = true;
  try {
    const plan = await api("/operations/storage-plan", {
      method: "POST",
    });
    toast(
      `Storage plan: ${plan.placement_counts.postgresql} PostgreSQL tables, ${plan.foreign_key_count} verified relationships`,
    );
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function exportAnalyticalHistory() {
  const button = $("#export-analytical");
  button.disabled = true;
  try {
    const result = await api(
      "/operations/storage-export-analytical",
      { method: "POST" },
    );
    toast(
      `Export ${result.export_id}: ${formatNumber(result.verified_row_count, 0)} rows verified`,
    );
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

function drawCurve(points) {
  const canvas = $("#equity-curve");
  if (!canvas) return;
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (!points.length) {
    context.fillStyle = "#66716a";
    context.font = "13px system-ui";
    context.fillText("No filled-trade equity points for this run.", 18, 82);
    return;
  }
  const values = points.map((point) => Number(point.equity));
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const range = Math.max(maximum - minimum, 1);
  context.strokeStyle = "#16734a";
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
    context.fillStyle = "#66716a";
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
    context.fillStyle = "#66716a";
    context.fillText(row.label, 12, y + 15);
    context.fillStyle = value < 0 ? "#a83a35" : options.color || "#16734a";
    context.fillRect(left, y, Math.max(width, 2), 16);
    context.fillStyle = "#17201b";
    context.fillText(formatNumber(value, options.digits ?? 2), left + width + 6, y + 13);
  });
}

function drawWorkflowChart(counts) {
  drawBarChart("workflow-chart", [
    { label: "Signals", value: counts.signals },
    { label: "Risk", value: counts.risk_decisions },
    { label: "Orders", value: counts.orders },
    { label: "Fills", value: counts.fills },
  ], { digits: 0, color: "#2c5f8a" });
}

function drawPnlChart(run) {
  drawBarChart("pnl-chart", [
    { label: "Net P&L", value: run.net_pnl },
    { label: "Drawdown", value: -Math.abs(Number(run.max_drawdown || 0)) },
    { label: "Trades", value: run.total_trades },
    { label: "Return %", value: run.return_pct },
  ], { digits: 2, color: "#16734a" });
}

function renderApprovals() {
  const container = $("#approval-list");
  if (!state.approvals.length) {
    container.innerHTML = `<div class="empty-state">No pending approvals.</div>`;
    return;
  }
  container.innerHTML = state.approvals.map((approval) => `
    <article class="approval-item" data-approval-id="${escapeHtml(approval.approval_id)}">
      <div class="item-header">
        <div>
          <strong>${escapeHtml(approval.requested_action)}</strong>
          <p>Intent ${escapeHtml(approval.intent_id)} · requested by ${escapeHtml(approval.requested_by)}</p>
        </div>
        <span class="status-pill">pending</span>
      </div>
      <div class="approval-actions">
        <button class="decision-button approve" data-decision="approve">Approve</button>
        <button class="decision-button reject" data-decision="reject">Reject</button>
      </div>
    </article>
  `).join("");
  container.querySelectorAll("[data-decision]").forEach((button) => {
    button.addEventListener("click", () => decideApproval(
      button.closest("[data-approval-id]").dataset.approvalId,
      button.dataset.decision === "approve",
    ));
  });
}

function renderPaperTrading() {
  renderPaperRunOptions();
  renderPaperIntents();
}

function renderPaperRunOptions() {
  const runSelect = $("#paper-run");
  if (!runSelect) return;
  const semiAutoRuns = state.runs.filter((run) => (
    run.execution_mode === "semi_auto"
  ));
  runSelect.innerHTML = semiAutoRuns.length
    ? semiAutoRuns.map((run) => `
      <option value="${escapeHtml(run.run_id)}">${escapeHtml(run.run_id)} - ${escapeHtml(run.strategy)}</option>
    `).join("")
    : `<option value="">No semi-auto runs yet</option>`;
  runSelect.disabled = !semiAutoRuns.length;
  $("#prepare-paper-intent").disabled = !semiAutoRuns.length || !hasRole("researcher");
  if (semiAutoRuns.length) {
    loadPaperDecisions(semiAutoRuns[0].run_id);
  } else {
    $("#paper-decision").innerHTML = `<option value="">Run a semi-auto backtest first</option>`;
  }
}

async function loadPaperDecisions(runId) {
  const select = $("#paper-decision");
  select.innerHTML = `<option value="">Loading approved decisions...</option>`;
  try {
    const risk = await api(`/runs/${encodeURIComponent(runId)}/risk`);
    const decisions = (risk.decisions || []).filter((decision) => (
      decision.approved
      && decision.checks?.execution_mode_check?.mode === "semi_auto"
      && Number(decision.approved_quantity || 0) > 0
    ));
    select.innerHTML = decisions.length
      ? decisions.map((decision) => {
        const side = decision.signal_type === "exit" ? "SELL" : "BUY";
        const label = `${side} ${decision.symbol || "symbol"} qty ${decision.approved_quantity} - ${decision.signal_reason || decision.reason}`;
        return `<option value="${escapeHtml(decision.decision_id)}"
          data-symbol="${escapeHtml(decision.symbol || "")}"
          data-side="${escapeHtml(side)}"
          data-quantity="${escapeHtml(decision.approved_quantity)}"
          data-strategy="${escapeHtml(state.runs.find((run) => run.run_id === runId)?.strategy || "IIMC")}">${escapeHtml(label)}</option>`;
      }).join("")
      : `<option value="">No approved semi-auto decisions</option>`;
    $("#prepare-paper-intent").disabled = !decisions.length || !hasRole("researcher");
    $("#paper-helper").textContent = decisions.length
      ? "Preparing an intent creates an analyzer-ready paper order from a risk-approved decision."
      : "This run has no approved semi-auto decisions. Run another semi-auto backtest or adjust strategy parameters.";
  } catch (error) {
    select.innerHTML = `<option value="">Could not load decisions</option>`;
    $("#paper-helper").textContent = error.message;
    $("#prepare-paper-intent").disabled = true;
  }
}

async function submitPaperIntent(event) {
  event.preventDefault();
  const option = $("#paper-decision").selectedOptions[0];
  if (!option?.value) return;
  const button = $("#prepare-paper-intent");
  button.disabled = true;
  try {
    const orderType = $("#paper-order-type").value;
    const payload = {
      decision_id: option.value,
      symbol: option.dataset.symbol,
      exchange: $("#paper-exchange").value,
      side: option.dataset.side,
      product: $("#paper-product").value,
      order_type: orderType,
      quantity: Number(option.dataset.quantity),
      strategy_name: option.dataset.strategy || "IIMC",
      limit_price: $("#paper-limit-price").value ? Number($("#paper-limit-price").value) : null,
      trigger_price: $("#paper-trigger-price").value ? Number($("#paper-trigger-price").value) : null,
      requested_by: state.principal?.username || "workspace_user",
    };
    const intent = await api("/sandbox/intents", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    toast(`Intent ${intent.intent_id} prepared for OpenAlgo analyzer`);
    await loadOverview();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = !hasRole("researcher");
  }
}

function renderPaperIntents() {
  const table = $("#paper-intents-table");
  if (!table) return;
  table.innerHTML = state.sandboxIntents.length
    ? state.sandboxIntents.map((intent) => `
      <tr data-intent-id="${escapeHtml(intent.intent_id)}">
        <td>${escapeHtml(intent.intent_id)}<br><small>${escapeHtml(intent.run_id || "-")}</small></td>
        <td>${escapeHtml(intent.symbol)} ${escapeHtml(intent.exchange)}</td>
        <td>${escapeHtml(intent.side)}</td>
        <td>${formatNumber(intent.quantity, 0)}</td>
        <td><span class="status-pill">${escapeHtml(intent.status)}</span></td>
        <td>${escapeHtml(intent.broker_order_id || "-")}</td>
        <td>${renderIntentActions(intent)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="7">No OpenAlgo sandbox intents prepared yet.</td></tr>`;
  table.querySelectorAll("[data-intent-action]").forEach((button) => {
    button.addEventListener("click", () => handleIntentAction(
      button.dataset.intentAction,
      button.closest("[data-intent-id]").dataset.intentId,
    ));
  });
}

function renderIntentActions(intent) {
  const actions = [];
  if (intent.status === "approved" && hasRole("researcher")) {
    actions.push(`<button class="secondary-button intent-action" data-intent-action="submit">Submit</button>`);
  }
  if (["submitted", "open", "pending"].includes(intent.status) && hasRole("researcher")) {
    actions.push(`<button class="secondary-button intent-action" data-intent-action="reconcile">Reconcile</button>`);
  }
  if (!actions.length) return "-";
  return actions.join(" ");
}

async function handleIntentAction(action, intentId) {
  try {
    if (action === "submit") {
      await api(`/sandbox/intents/${encodeURIComponent(intentId)}/submit`, {
        method: "POST",
        body: JSON.stringify({ actor: state.principal?.username || "workspace_user" }),
      });
      toast(`Intent ${intentId} submitted to OpenAlgo analyzer`);
    }
    if (action === "reconcile") {
      await api(`/sandbox/intents/${encodeURIComponent(intentId)}/reconcile`, {
        method: "POST",
        body: JSON.stringify({ actor: state.principal?.username || "workspace_user" }),
      });
      toast(`Intent ${intentId} reconciled`);
    }
    await loadOverview();
  } catch (error) {
    toast(error.message);
  }
}

async function decideApproval(approvalId, approved) {
  const reason = window.prompt(
    approved ? "Reason for approval" : "Reason for rejection",
    approved ? "Reviewed for OpenAlgo sandbox execution" : "",
  );
  if (!reason) return;
  try {
    await api(`/approvals/${encodeURIComponent(approvalId)}/decision`, {
      method: "POST",
      body: JSON.stringify({
        approved,
        decided_by: "workspace_user",
        reason,
      }),
    });
    toast(approved ? "Approval recorded" : "Rejection recorded");
    await loadOverview();
  } catch (error) {
    toast(error.message);
  }
}

function renderDatasets() {
  const container = $("#dataset-list");
  if (!state.datasets.length) {
    container.innerHTML = `<div class="empty-state">No governed datasets.</div>`;
    return;
  }
  container.innerHTML = state.datasets.map((dataset) => `
    <article class="dataset-item" data-dataset-id="${escapeHtml(dataset.dataset_id)}">
      <div class="item-header">
        <div>
          <strong>${escapeHtml(dataset.dataset_id)}</strong>
          <p>${escapeHtml(dataset.symbol)} · ${escapeHtml(dataset.exchange)} · ${escapeHtml(dataset.interval)}</p>
        </div>
        <button class="secondary-button freshness-button">Assess current freshness</button>
      </div>
      <div class="dataset-meta">
        <div><span>Rows</span><strong>${formatNumber(dataset.row_count, 0)}</strong></div>
        <div><span>Quality</span><strong>${escapeHtml(dataset.quality.status)}</strong></div>
        <div><span>Coverage start</span><strong>${escapeHtml(dataset.start_ts)}</strong></div>
        <div><span>Coverage end</span><strong>${escapeHtml(dataset.end_ts)}</strong></div>
      </div>
      <div class="freshness-slot"></div>
    </article>
  `).join("");
  container.querySelectorAll(".freshness-button").forEach((button) => {
    button.addEventListener("click", () => assessFreshness(button.closest("[data-dataset-id]")));
  });
}

async function assessFreshness(article) {
  const datasetId = article.dataset.datasetId;
  try {
    const result = await api(`/datasets/${encodeURIComponent(datasetId)}/freshness?purpose=current_market`);
    const slot = article.querySelector(".freshness-slot");
    slot.className = `freshness-result ${result.status}`;
    slot.textContent = `${result.status.toUpperCase()}: ${result.reason}`;
  } catch (error) {
    toast(error.message);
  }
}

async function submitOhlcvImport(event) {
  event.preventDefault();
  const button = $("#import-ohlcv");
  const resultBox = $("#ohlcv-import-result");
  button.disabled = true;
  resultBox.classList.remove("hidden");
  try {
    const candles = JSON.parse($("#ohlcv-candles").value);
    if (!Array.isArray(candles)) throw new Error("Candles must be a JSON array");
    const result = await api("/datasets/ohlcv", {
      method: "POST",
      body: JSON.stringify({
        dataset_id: $("#ohlcv-dataset-id").value.trim(),
        asset_class: $("#ohlcv-asset-class").value,
        symbol: $("#ohlcv-symbol").value.trim(),
        exchange: $("#ohlcv-exchange").value.trim(),
        interval: $("#ohlcv-interval").value.trim(),
        candles,
      }),
    });
    resultBox.textContent = JSON.stringify(result, null, 2);
    toast(`Imported ${result.row_count} governed OHLCV candles`);
    await loadOverview();
  } catch (error) {
    resultBox.textContent = JSON.stringify({ ok: false, message: error.message }, null, 2);
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function submitFeatureImport(event) {
  event.preventDefault();
  const button = $("#import-features");
  const resultBox = $("#feature-import-result");
  button.disabled = true;
  resultBox.classList.remove("hidden");
  try {
    const observations = JSON.parse($("#feature-observations").value);
    if (!Array.isArray(observations)) throw new Error("Observations must be a JSON array");
    const result = await api("/datasets/features", {
      method: "POST",
      body: JSON.stringify({
        dataset_id: $("#feature-dataset-id").value.trim(),
        symbol: $("#feature-symbol").value.trim(),
        exchange: $("#feature-exchange").value.trim(),
        observations,
      }),
    });
    resultBox.textContent = JSON.stringify(result, null, 2);
    toast(`Imported ${result.row_count} point-in-time feature observations`);
    await loadOverview();
  } catch (error) {
    resultBox.textContent = JSON.stringify({ ok: false, message: error.message }, null, 2);
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function submitOptionsFeatureDerivation(event) {
  event.preventDefault();
  const button = $("#derive-options-features");
  const resultBox = $("#options-feature-result");
  const sourceDataset = $("#options-feature-source").value.trim();
  const featureNames = $("#options-feature-names").value
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean);
  button.disabled = true;
  resultBox.classList.remove("hidden");
  try {
    if (!featureNames.length) throw new Error("Enter at least one options feature");
    const result = await api(`/datasets/options/${encodeURIComponent(sourceDataset)}/derive-features`, {
      method: "POST",
      body: JSON.stringify({
        feature_dataset_id: $("#options-feature-dataset-id").value.trim(),
        feature_names: featureNames,
        availability_delay_seconds: Number($("#options-feature-delay").value || 0),
      }),
    });
    resultBox.textContent = JSON.stringify(result, null, 2);
    toast(`Derived ${result.row_count} governed options feature observations`);
    await loadOverview();
  } catch (error) {
    resultBox.textContent = JSON.stringify({ ok: false, message: error.message }, null, 2);
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

function wireEvents() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  $("#chat-form").addEventListener("submit", submitChat);
  $("#readiness-form").addEventListener("submit", submitReadiness);
  $("#market-news-form").addEventListener("submit", submitMarketNews);
  $("#create-research-brief").addEventListener("click", createResearchBrief);
  $("#toggle-live-dashboard").addEventListener(
    "click",
    () => setAutoRefresh(!state.autoRefresh),
  );
  $("#backtest-form").addEventListener("submit", submitBacktest);
  $("#custom-strategy-form").addEventListener("submit", submitCustomStrategySpec);
  $("#validate-custom-strategy").addEventListener("click", validateCustomStrategySpec);
  $("#run-custom-strategy").addEventListener("click", runSelectedCustomStrategySpec);
  $("#custom-strategy-spec-select").addEventListener("change", renderSelectedCustomStrategySpec);
  $("#custom-strategy-template").addEventListener("change", () => {
    $("#custom-strategy-description").value = (
      `Local governed ${$("#custom-strategy-template").selectedOptions[0].textContent} strategy.`
    );
    syncCustomStrategyRules();
  });
  syncCustomStrategyRules();
  $("#paper-intent-form").addEventListener("submit", submitPaperIntent);
  $("#paper-run").addEventListener("change", (event) => {
    if (event.target.value) loadPaperDecisions(event.target.value);
  });
  $("#knowledge-search-form").addEventListener("submit", submitKnowledgeSearch);
  $("#ohlcv-import-form").addEventListener("submit", submitOhlcvImport);
  $("#feature-import-form").addEventListener("submit", submitFeatureImport);
  $("#options-feature-form").addEventListener("submit", submitOptionsFeatureDerivation);
  $("#login-form").addEventListener("submit", submitLogin);
  $("#logout-button").addEventListener("click", logout);
  $("#compare-runs").addEventListener("click", compareSelectedRuns);
  $("#experiment-form").addEventListener("submit", submitExperiment);
  $("#portfolio-create-form").addEventListener("submit", submitPortfolio);
  $("#create-backup").addEventListener("click", createBackup);
  $("#run-ai-eval").addEventListener("click", runAiEvaluation);
  $("#run-retrieval-eval").addEventListener(
    "click",
    runRetrievalEvaluation,
  );
  $("#preview-retention").addEventListener(
    "click",
    previewRetention,
  );
  $("#evaluate-alerts").addEventListener("click", evaluateAlerts);
  $("#generate-storage-plan").addEventListener(
    "click",
    generateStoragePlan,
  );
  $("#export-analytical").addEventListener(
    "click",
    exportAnalyticalHistory,
  );
  document.querySelectorAll(".snapshot-button").forEach((button) => {
    button.addEventListener("click", () => (
      captureOpenAlgoSnapshot(button.dataset.snapshotType)
    ));
  });
  $("#chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });
  $("#new-session").addEventListener("click", () => {
    state.sessionId = `session_ui_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
    $("#messages").innerHTML = "";
    appendMessage("assistant", "New audited session started. What would you like to inspect?");
    $("#evidence-content").classList.add("hidden");
    $("#evidence-empty").classList.remove("hidden");
  });
  ["refresh-overview", "refresh-operator", "refresh-runs", "refresh-experiments", "refresh-portfolios", "refresh-approvals", "refresh-data", "refresh-openalgo", "refresh-operations"].forEach((id) => {
    $(`#${id}`).addEventListener("click", async () => {
      try {
        state.operations = null;
        await loadOverview();
        toast("Workspace refreshed");
      } catch (error) {
        toast(error.message);
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
  try {
    await loadOverview();
  } catch (error) {
    toast(error.message);
  }
}

start();
