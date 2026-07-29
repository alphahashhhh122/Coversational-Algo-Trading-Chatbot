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

// Backend errors are written for whoever has to debug them. On screen they
// need to say what broke and what to do next — "OpenAlgo is unavailable at the
// configured base URL" tells a trader nothing they can act on.
//
// Anything unrecognised passes through unchanged rather than being replaced by
// a vague apology: a specific message we did not anticipate is still more use
// than "something went wrong".
const ERROR_TRANSLATIONS = [
  [/unavailable at the configured base URL|OpenAlgo is unavailable/i,
   "Your broker connection isn't running. Start OpenAlgo, then try again."],
  [/rejected the configured API key|authentication|401|403/i,
   "Your broker rejected the saved key. Log in to OpenAlgo again to refresh it."],
  [/not configured|no api key|credentials are not configured/i,
   "That needs your broker connected first — add it in Settings."],
  [/no stored (market )?data|dataset .* not found|no candles/i,
   "There's no stored price history for that yet. Ask for it in chat, or use “Fetch more history” on the Data tab."],
  [/rate limit|429/i,
   "Too many requests at once. Wait a moment and try again."],
  [/timed out|timeout/i,
   "That took too long to respond. It may still be running — try again in a moment."],
  [/HTTP 5\d\d|internal server error/i,
   "Something went wrong on the platform's side. The details are in the server log."],
  [/failed to fetch|networkerror|load failed/i,
   "Couldn't reach the platform. Check that it's still running."],
];

function friendlyError(error) {
  const raw = String(error?.message ?? error ?? "").trim();
  if (!raw) return "Something went wrong, and no reason was given.";
  for (const [pattern, plain] of ERROR_TRANSLATIONS) {
    if (pattern.test(raw)) return plain;
  }
  return raw;
}

// "1 entrant(s)" is the sort of thing that tells a reader the screen was not
// finished. Pluralise properly; it costs one function.
function plural(count, singular, pluralForm) {
  const n = Number(count) || 0;
  return `${n} ${n === 1 ? singular : (pluralForm || singular + "s")}`;
}

export { friendlyError, plural, state, $, api, escapeHtml, toast, showLogin };
