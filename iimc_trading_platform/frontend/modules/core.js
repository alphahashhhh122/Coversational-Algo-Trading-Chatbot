// The handful of things every view needs: shared state, the fetch wrapper
// that speaks this API's error shape, DOM helpers, and the login gate.
// Kept deliberately small - whatever drifts in here is global again.

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


function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  window.setTimeout(() => element.classList.remove("show"), 2800);
}


function showLogin() {
  $("#auth-overlay").classList.remove("hidden");
}

export { state, $, api, escapeHtml, toast, showLogin };
