// Isolated UI state tests only. All account/provider responses are mocked;
// these checks do not claim a real third-party account has been read.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

const frontend = path.resolve(__dirname, "..");
class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
const tick = () => new Promise((resolve) => setImmediate(resolve));
const reporting = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(frontend, "lib/reporting.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: reporting, Intl, Date });

function harness(file, name, initial = [], api = {}, language = "en") {
  const state = initial.slice();
  const effects = [];
  let cursor = 0;
  const exports = {};
  const messages = require(path.join(frontend, `lib/i18n/ui.${language}.json`));
  const serverMessages = language === "en" ? {} : require(path.join(frontend, `lib/i18n/server.${language}.json`)).exact;
  const t = (key, args = {}) => Object.entries(args).reduce((value, [k, v]) => value.replaceAll(`{${k}}`, String(v)), messages[key] ?? key);
  const source = ts.transpileModule(fs.readFileSync(path.join(frontend, file), "utf8") + `\nexport { ${name} as TestedComponent };`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(source, {
    exports, Intl, Date, AbortController, URLSearchParams,
    require(id) {
      if (id === "react/jsx-runtime") return require(id);
      if (id === "react") return {
        useState(value) {
          const at = cursor++;
          if (!(at in state)) state[at] = value;
          return [state[at], (next) => { state[at] = typeof next === "function" ? next(state[at]) : next; }];
        },
        useEffect(callback) { effects.push(callback); },
        useMemo(callback) { return callback(); },
      };
      if (id === "next/link") return { default: "a" };
      if (id.endsWith("/i18n")) return { useI18n: () => ({ t, tx: (v) => serverMessages[v] ?? v, locale: language === "en" ? "en-US" : language, lang: language }) };
      if (id.endsWith("/DataModeContext")) return { useDataMode: () => ({ mode: "live", identity: { email: "judge@example.test" }, session: { workspace_id: "0123456789abcdef0123456789abcdef" }, capabilities: { public_demo_only: true, live_self_service_enabled: true } }) };
      if (id.endsWith("/reporting")) return reporting;
      if (id.endsWith("/api")) return { asNumber: Number, ApiError, ...api };
      if (id.endsWith("/PageState")) return { PageState: "page-state" };
      if (id.endsWith("/ActionLedger")) return { ActionLedger: "ledger", StatusPill: "pill" };
      if (id.endsWith("/HistoryList")) return { HistoryList: "history-list" };
      if (id.endsWith("/Segmented")) return { Segmented: "segmented" };
      throw new Error(`Unexpected import ${id}`);
    },
  });
  return {
    state, messages,
    value: exports.TestedComponent,
    render(props = {}) { cursor = 0; return exports.TestedComponent(props); },
    runEffects() { for (const effect of effects.splice(0)) effect(); },
  };
}

function nodes(node) {
  if (node == null || typeof node === "boolean") return [];
  if (Array.isArray(node)) return node.flatMap(nodes);
  if (typeof node !== "object") return [node];
  return [node, ...nodes(node.props?.children)];
}
function text(node) { return nodes(node).filter((n) => typeof n === "string" || typeof n === "number").join(" "); }
function loadButton(node) { return nodes(node).find((n) => n?.type === "button" && n.props.className === "button"); }
const center = (count) => ({ connections: ["stripe", "google_ads", "meta_ads"].map((provider, index) => ({ provider, configured: index < count })) });
const fixture = {
  growth_score: { score: 73, band: "promising" },
  revenue: { mrr: 100, currency: "CNY" },
  google_ads: { roas: null, spend: 20, currency: "EUR" },
  meta_ads: { roas: 2, spend: 3000, currency: "EUR" },
  generated_at: "2026-10-18T00:00:00Z", audit_recorded: false,
};
const readyState = (report = null) => [report, false, null, center(3), false, null, null, null, false, false];

test("Today 0/partial/full setup is honest and does not read provider data", () => {
  for (const count of [0, 1, 2, 3]) {
    const h = harness("app/page.tsx", "LiveToday", [center(count), false], { getJson: () => assert.fail("render must not read providers") });
    const view = h.render();
    assert.match(text(view), new RegExp(`${count} of 3 configured`));
    assert.match(text(view), /Configured does not mean tested/);
    assert.equal(nodes(view).find((n) => n?.props?.className === "button").props.href, count === 3 ? "/dashboard" : "/connections");
  }
});

test("Growth identifies missing sources and blocks aggregate reads before all three are configured", async () => {
  for (const count of [0, 1, 2]) {
    const initial = readyState(); initial[3] = center(count);
    const h = harness("app/dashboard/page.tsx", "LiveGrowth", initial, { requestJson: () => assert.fail("incomplete setup must not read providers") });
    const view = h.render();
    assert.equal(loadButton(view).props.disabled, true);
    await loadButton(view).props.onClick();
    for (const provider of ["Stripe", "Google Ads", "Meta Ads"].slice(count)) assert.ok(text(view).includes(provider));
    assert.ok(nodes(view).some((n) => n?.type === "a" && n.props.href === "/connections"));
  }
});

test("initial audit failure never blocks Growth readiness or its explicit load button", async () => {
  const paths = [];
  const h = harness("app/dashboard/page.tsx", "LiveGrowth", [], { requestJson: async (pathname) => {
    paths.push(pathname);
    if (pathname === "/api/v1/connections") return center(3);
    throw new ApiError("audit unavailable", 404);
  } });
  h.render(); h.runEffects(); await tick();
  const view = h.render();
  assert.equal(loadButton(view).props.disabled, false);
  assert.ok(text(view).includes(h.messages["growth.live.auditUnavailable"]));
  assert.equal(paths.some((value) => value.includes("growth-intelligence")), false);
});

test("Growth blocks mismatched ad currencies before a read but allows a different Stripe currency", async () => {
  const initial = readyState();
  initial[3].connections[0].currency = "CNY";
  initial[3].connections[1].currency = "EUR";
  initial[3].connections[2].currency = "USD";
  const h = harness("app/dashboard/page.tsx", "LiveGrowth", initial, { requestJson: () => assert.fail("currency mismatch must not read providers") });
  let view = h.render();
  assert.equal(loadButton(view).props.disabled, true);
  assert.ok(text(view).includes(h.messages["growth.live.currencyMismatch"]));
  await loadButton(view).props.onClick();
  h.state[3].connections[2].currency = "EUR";
  view = h.render();
  assert.equal(loadButton(view).props.disabled, false);
});

test("successful Growth report survives failed audit refresh and shows MRR, separate currency codes and audit warning", async () => {
  let reads = 0;
  const h = harness("app/dashboard/page.tsx", "LiveGrowth", readyState(), { requestJson: async (pathname) => {
    if (pathname.includes("growth-intelligence")) { reads += 1; return fixture; }
    throw new ApiError("audit unavailable", 502);
  } });
  await loadButton(h.render()).props.onClick(); await tick();
  const view = h.render();
  assert.equal(reads, 1);
  assert.equal(h.state[0], fixture);
  assert.equal(h.state[2], null);
  assert.match(text(view), /Monthly recurring revenue \(MRR\)/);
  for (const code of ["CNY", "EUR"]) assert.ok(text(view).includes(code));
  assert.ok(text(view).includes(h.messages["growth.live.auditNotSaved"]));
  assert.ok(text(view).includes(h.messages["growth.live.auditUnavailable"]));
  assert.ok(!text(view).includes(h.messages["live.requestError"]));
});

test("Growth failed refresh retains the previous report with an explicit stale-result warning", async () => {
  const h = harness("app/dashboard/page.tsx", "LiveGrowth", readyState(fixture), { requestJson: async () => { throw new ApiError("source unavailable", 502); } });
  await loadButton(h.render()).props.onClick();
  assert.equal(h.state[0], fixture);
  assert.ok(text(h.render()).includes(h.messages["growth.live.previousReport"]));
});

test("Growth surfaces safe status-specific 401/403/404/409/429 guidance", async () => {
  for (const [status, key] of [[401, "live.authRequired"], [403, "live.authRequired"], [404, "live.serviceUnavailable"], [409, "growth.live.configurationIssue"], [429, "live.quota"]]) {
    const h = harness("app/dashboard/page.tsx", "LiveGrowth", readyState(), { requestJson: async () => { throw new ApiError("redacted", status); } });
    await loadButton(h.render()).props.onClick(); await tick();
    assert.equal(h.state[2], key);
  }
});

test("Growth translates known provider credential failures without exposing raw errors or confusing app login", async () => {
  for (const language of ["en", "zh-Hans", "zh-Hant"]) {
    for (const [message, key] of [
      ["Google Ads sign-in has expired or lost permission.", "growth.live.googleAuthFailed"],
      ["Stripe rejected the saved credentials.", "growth.live.stripeAuthFailed"],
      ["Meta Ads rejected the saved credentials.", "growth.live.metaAuthFailed"],
    ]) {
      for (const status of [502, 403]) {
        const h = harness("app/dashboard/page.tsx", "LiveGrowth", readyState(), { requestJson: async () => { throw new ApiError(`${message} private-provider-detail`, status); } }, language);
        await loadButton(h.render()).props.onClick();
        const expected = status === 403 ? "live.authRequired" : key;
        assert.equal(h.state[2], expected);
        assert.ok(h.messages[expected]);
        assert.ok(text(h.render()).includes(h.messages[expected]));
        assert.doesNotMatch(text(h.render()), /private-provider-detail/);
      }
    }
  }
});

const stripe = { provider: "stripe", state: "needs_configuration", display_name: "Stripe", setup_label: "", note: "", setup_steps: [], accounts: [], configured: false, supports_live_test: true, currency: "CNY" };
const rowState = (currency = "CNY") => ["rk_test", {}, {}, false, "", null, false, currency, 0];
const submit = (view) => nodes(view).find((n) => n?.type === "form").props.onSubmit({ preventDefault() {} });

const aiConnections = (configured = []) => ["openai", "gemini", "gemini_vertex", "perplexity"].map((provider) => ({
  ...stripe, provider, display_name: provider, currency: null,
  configured: configured.includes(provider), state: configured.includes(provider) ? "connected" : "needs_configuration",
}));

test("AI visibility connections count OpenAI, Gemini and Perplexity once, with either Gemini method sufficient", () => {
  for (const language of ["en", "zh-Hans", "zh-Hant"]) {
    for (const [configured, expected] of [
      [[], 0], [["gemini"], 1], [["gemini_vertex"], 1], [["gemini", "gemini_vertex"], 1],
      [["openai", "gemini", "perplexity"], 3], [["openai", "gemini_vertex", "perplexity"], 3],
      [["openai", "gemini", "gemini_vertex", "perplexity"], 3],
    ]) {
      const h = harness("app/connections/page.tsx", "ConnectionsPage", [{ tenant_id: "test-workspace", connections: aiConnections(configured) }, null], {}, language);
      const section = nodes(h.render()).find((n) => n?.type === "section" && nodes(n).some((child) => child?.type === "h2" && text(child) === h.messages["connections.ai"]));
      assert.ok(text(section).includes(h.messages["connections.ready"].replace("{n}", String(expected)).replace("{total}", "3")), `${language}: ${configured}`);
      assert.equal(nodes(section).filter((n) => n?.type?.name === "GeminiConnectionRow").length, 1);
      assert.equal(nodes(section).filter((n) => n?.type?.name === "ConnectionRow").length, 2);
    }
  }
});

test("Gemini opens an already configured method and switching settings cannot alter saved credentials", () => {
  for (const [configured, expected] of [[[], "gemini"], [["gemini"], "gemini"], [["gemini_vertex"], "gemini_vertex"], [["gemini", "gemini_vertex"], "gemini_vertex"]]) {
    const items = aiConnections(configured).filter((item) => ["gemini", "gemini_vertex"].includes(item.provider));
    const before = JSON.stringify(items);
    const h = harness("app/connections/page.tsx", "GeminiConnectionRow", [], {
      requestJson: () => assert.fail("choosing a Gemini settings form must not send a request"),
    });
    const props = { items, onChanged: () => assert.fail("switching method must not save or refresh") };
    let view = h.render(props);
    const selector = nodes(view).find((n) => n?.type === "select");
    assert.equal(selector.props.value, expected);
    const other = expected === "gemini" ? "gemini_vertex" : "gemini";
    selector.props.onChange({ target: { value: other } });
    view = h.render(props);
    const row = nodes(view).find((n) => n?.type?.name === "ConnectionRow");
    assert.equal(row.props.item.provider, other);
    assert.equal(row.key, other, "separate form instances cannot leak draft credentials between methods");
    assert.equal(JSON.stringify(items), before);
  }
});

test("Gemini checks and disconnects only the selected saved method", async () => {
  for (const provider of ["gemini", "gemini_vertex"]) {
    const requests = [];
    const h = harness("app/connections/page.tsx", "ConnectionRow", [], {
      requestJson: async (pathname, init) => { requests.push({ pathname, method: init.method }); return { success: true, checked_live: true }; },
    });
    const item = aiConnections(["gemini", "gemini_vertex"]).find((connection) => connection.provider === provider);
    const props = { item, onChanged: async () => {} };
    const check = nodes(h.render(props)).find((n) => n?.type === "button" && n.props.children === h.messages["connections.test"]);
    await check.props.onClick(); await tick();
    const disconnect = nodes(h.render(props)).find((n) => n?.type === "button" && n.props.children === h.messages["connections.disconnect"]);
    disconnect.props.onClick(); await tick();
    assert.deepEqual(requests, [
      { pathname: `/api/v1/connections/${provider}/test`, method: "POST" },
      { pathname: `/api/v1/connections/${provider}`, method: "DELETE" },
    ]);
  }
});

test("Gemini alternative setup and routing explanations are available in every language", () => {
  for (const language of ["en", "zh-Hans", "zh-Hant"]) {
    const h = harness("app/connections/page.tsx", "GeminiConnectionRow", [], {}, language);
    const view = h.render({ items: aiConnections(["gemini_vertex"]).filter((item) => ["gemini", "gemini_vertex"].includes(item.provider)), onChanged: async () => {} });
    for (const key of ["connections.geminiMethod", "connections.geminiAiStudio", "connections.geminiVertex", "connections.geminiAlternativesHint", "connections.geminiRoutingHint"]) {
      assert.ok(h.messages[key], `${language}: ${key}`);
      assert.ok(text(view).includes(h.messages[key]));
    }
  }
});

const linkedVertex = {
  ...stripe, provider: "gemini_vertex", display_name: "Gemini · Vertex AI", configured: true, state: "connected",
  auth_method: "Enterprise connection · Linked project identity", configuration_source: "tenant_vault",
  project_id: "test-project", location: "global", currency: null,
  setup_steps: ["Runtime identity linked to this workspace", "Test Google Search grounding", "Disconnect to revoke workspace access"],
};

test("linked Vertex runtime identity needs no API key to check and shows localized replacement-only guidance", async () => {
  for (const language of ["en", "zh-Hans", "zh-Hant"]) {
    const requests = [];
    const h = harness("app/connections/page.tsx", "ConnectionRow", [], {
      requestJson: async (pathname, init) => { requests.push({ pathname, method: init.method }); return { success: true, checked_live: true }; },
    }, language);
    const props = { item: linkedVertex, onChanged: async () => {} };
    const view = h.render(props);
    for (const key of ["connections.vertexLinkedHint", "connections.vertexReplacementKey", "connections.vertexReplace"]) assert.ok(text(view).includes(h.messages[key]));
    assert.ok(!text(view).includes(h.messages["connections.vertexKey"]));
    const server = language === "en" ? {} : require(path.join(frontend, `lib/i18n/server.${language}.json`)).exact;
    for (const source of [linkedVertex.auth_method, ...linkedVertex.setup_steps]) {
      if (language !== "en") assert.ok(server[source], `${language}: ${source}`);
      assert.ok(text(view).includes(server[source] ?? source));
    }
    const replace = nodes(view).find((n) => n?.type === "button" && n.props.type === "submit");
    assert.equal(replace.props.disabled, true, "empty replacement key must not overwrite linked identity");
    submit(view); await tick();
    assert.equal(requests.length, 0);
    const check = nodes(view).find((n) => n?.type === "button" && n.props.children === h.messages["connections.test"]);
    assert.equal(check.props.disabled, false);
    await check.props.onClick(); await tick();
    assert.deepEqual(requests, [{ pathname: "/api/v1/connections/gemini_vertex/test", method: "POST" }]);
  }
});

test("linked Vertex identity can be explicitly replaced with an API key without editing other Gemini credentials", async () => {
  const requests = [];
  const initial = rowState("");
  initial[1] = { projectId: "replacement-project", location: "global", apiKey: " synthetic-key " };
  const h = harness("app/connections/page.tsx", "ConnectionRow", initial, {
    requestJson: async (pathname, init) => { requests.push({ pathname, method: init.method, body: JSON.parse(init.body) }); return {}; },
  });
  const view = h.render({ item: linkedVertex, onChanged: async () => {} });
  assert.equal(nodes(view).find((n) => n?.type === "button" && n.props.type === "submit").props.disabled, false);
  submit(view); await tick();
  assert.deepEqual(requests, [{ pathname: "/api/v1/connections/gemini_vertex/config", method: "PUT", body: {
    project_id: "replacement-project", location: "global", api_key: "synthetic-key",
  } }]);
  assert.equal(h.state[1].apiKey, "");
});

test("Visibility requests exactly one Gemini source using the established configured-method priority", async () => {
  for (const [configured, expected] of [
    [["openai", "gemini", "perplexity"], ["openai", "gemini", "perplexity"]],
    [["openai", "gemini_vertex", "perplexity"], ["openai", "gemini_vertex", "perplexity"]],
    [["openai", "gemini", "gemini_vertex", "perplexity"], ["openai", "gemini_vertex", "perplexity"]],
  ]) {
    let body;
    const h = harness("app/visibility/page.tsx", "LiveVisibility", [{ connections: aiConnections(configured) }, null, "Test brand", "https://example.test", "Test category", "", null, false, ""], {
      requestJson: async (_pathname, init) => { body = JSON.parse(init.body); return { providers: [], results: [] }; },
      getJson: async () => ({ entries: [] }),
    });
    submit(h.render()); await tick();
    assert.deepEqual(body.providers, expected);
    assert.equal(body.mode, "live");
  }
});

test("Connections awaits metadata refresh, clears saved key, and retries failed status without replaying mutation", async () => {
  const requests = []; let refreshes = 0; let finishRefresh;
  const h = harness("app/connections/page.tsx", "ConnectionRow", rowState(), { requestJson: async (pathname, init) => { requests.push({ pathname, body: JSON.parse(init.body) }); return {}; } });
  const props = { item: stripe, onChanged: () => { refreshes += 1; return refreshes === 1 ? new Promise((_resolve, reject) => { finishRefresh = reject; }) : Promise.resolve(); } };
  submit(h.render(props)); await tick();
  assert.equal(h.state[3], true, "operation remains busy while metadata is loading");
  assert.deepEqual(requests[0].body, { secret: "rk_test", currency: "CNY" });
  finishRefresh(new ApiError("metadata unavailable", 502)); await tick();
  assert.equal(h.state[0], "");
  assert.equal(h.state[6], true);
  assert.equal(h.state[4], h.messages["connections.saved"]);
  const view = h.render(props);
  assert.ok(text(view).includes(h.messages["connections.refreshFailed"]));
  const retry = nodes(view).find((n) => n?.type === "button" && n.props.children === h.messages["connections.retryStatus"]);
  retry.props.onClick(); await tick();
  assert.equal(requests.length, 1);
  assert.equal(refreshes, 2);
  assert.equal(h.state[6], false);
});

test("Connections separates configuration-service 409 from invalid credentials and never renders raw provider errors", async () => {
  const h = harness("app/connections/page.tsx", "ConnectionRow", rowState(), { requestJson: async () => { throw new ApiError("secret-sensitive-provider-error", 409); } });
  const props = { item: stripe, onChanged: async () => {} };
  submit(h.render(props)); await tick();
  assert.equal(h.state[4], h.messages["connections.configurationUnavailable"]);
  assert.doesNotMatch(text(h.render(props)), /secret-sensitive-provider-error/);
});

test("a failed live connection test explains a known currency mismatch without echoing raw provider errors", () => {
  const initial = rowState();
  initial[5] = { success: false, checked_live: true, message: "The account currency does not match the saved reporting currency. Update the connection currency and test again." };
  const h = harness("app/connections/page.tsx", "ConnectionRow", initial);
  const view = h.render({ item: { ...stripe, configured: true }, onChanged: async () => {} });
  assert.ok(text(view).includes(h.messages["connections.currencyMismatch"]));
});

test("Google Ads checks distinguish OAuth, account, user, scope and project access safely", () => {
  const cases = [
    ["Google OAuth could not refresh this connection.", "connections.googleOAuthRefreshFailed"],
    ["Google OAuth succeeded, but the authorized user cannot read this customer ID.", "connections.googleAccountAccessFailed"],
    ["Google OAuth succeeded, but Google Ads denied access to this customer.", "connections.googleAccountAccessFailed"],
    ["Google OAuth succeeded, but the authorizing Google user has no Google Ads account access.", "connections.googleUserAccessFailed"],
    ["Google OAuth succeeded without the Google Ads scope.", "connections.googleScopeFailed"],
    ["Google OAuth succeeded, but the OAuth client project is not approved for this Ads account.", "connections.googleProjectAccessFailed"],
  ];
  for (const language of ["en", "zh-Hans", "zh-Hant"]) {
    for (const [message, key] of cases) {
      const initial = rowState();
      initial[5] = { success: false, checked_live: true, message };
      const h = harness("app/connections/page.tsx", "ConnectionRow", initial, {}, language);
      const view = h.render({ item: { ...stripe, provider: "google_ads", configured: true }, onChanged: async () => {} });
      assert.ok(text(view).includes(h.messages[key]), `${language}: ${key}`);
      assert.doesNotMatch(text(view), /private|trigger|customer-id-and-message/);
    }
  }
});

test("each business connector sends its explicit currency and incomplete currency blocks saving", async () => {
  const h = harness("app/connections/page.tsx", "ConnectionRow", rowState(""), { requestJson: async () => ({}) });
  const view = h.render({ item: stripe, onChanged: async () => {} });
  assert.equal(nodes(view).find((n) => n?.type === "button" && n.props.type === "submit").props.disabled, true);
  let googleBody;
  const google = harness("app/connections/page.tsx", "GoogleAdsForm", ["access", {
    developer_token: "test-developer", customer_id: "123-456", login_customer_id: "", access_token: "test-token",
    client_id: "", client_secret: "", refresh_token: "", currency: "EUR",
  }], { requestJson: async (_pathname, init) => { googleBody = JSON.parse(init.body); } });
  submit(google.render({ item: { selected_account_id: null, currency: "EUR" }, busy: false, act: async (work) => { await work(); return true; } })); await tick();
  assert.equal(googleBody.currency, "EUR");
  assert.equal(googleBody.customer_id, "123456");
  assert.equal(google.state[1].access_token, "");
  let metaBody;
  const initial = rowState("JPY"); initial[2] = { accessToken: "test-token", adAccountId: "act_123", apiVersion: "v22.0" };
  const meta = harness("app/connections/page.tsx", "ConnectionRow", initial, { requestJson: async (_pathname, init) => { metaBody = JSON.parse(init.body); } });
  submit(meta.render({ item: { ...stripe, provider: "meta_ads" }, onChanged: async () => {} })); await tick();
  assert.equal(metaBody.currency, "JPY");
  assert.equal(meta.state[2].accessToken, "");
});

const googleForm = (changes = {}) => ({
  developer_token: "", customer_id: "123-456-7890", login_customer_id: "",
  access_token: "test-access", client_id: "test-client", client_secret: "test-secret",
  refresh_token: "test-refresh", currency: "USD", ...changes,
});
const googleProps = { item: { selected_account_id: null, currency: "USD" }, busy: false, act: async (work) => { await work(); return true; } };
const saveButton = (view) => nodes(view).find((n) => n?.type === "button" && n.props.type === "submit");

test("Google Ads saves either authentication method without a developer token and omits empty legacy fields", async () => {
  for (const method of ["access", "oauth"]) {
    for (const developer_token of ["", "   "]) {
      let request;
      const h = harness("app/connections/page.tsx", "GoogleAdsForm", [method, googleForm({ developer_token })], {
        requestJson: async (pathname, init) => { request = { pathname, method: init.method, body: JSON.parse(init.body) }; },
      });
      const view = h.render(googleProps);
      assert.equal(saveButton(view).props.disabled, false);
      submit(view); await tick();
      assert.equal(request.pathname, "/api/v1/connections/google_ads/config");
      assert.equal(request.method, "PUT");
      assert.equal(request.body.customer_id, "1234567890");
      assert.equal(Object.hasOwn(request.body, "developer_token"), false);
      assert.equal(Object.hasOwn(request.body, "login_customer_id"), false);
      assert.equal(Object.hasOwn(request.body, "access_token"), method === "access");
      for (const key of ["client_id", "client_secret", "refresh_token"]) assert.equal(Object.hasOwn(request.body, key), method === "oauth");
      for (const key of ["developer_token", "access_token", "client_secret", "refresh_token"]) assert.equal(h.state[1][key], "");
    }
  }
});

test("Google Ads retains explicitly supplied legacy developer tokens without making them required", async () => {
  let body;
  const h = harness("app/connections/page.tsx", "GoogleAdsForm", ["access", googleForm({ developer_token: " legacy-token ", login_customer_id: " 555-123-4567 " })], {
    requestJson: async (_pathname, init) => { body = JSON.parse(init.body); },
  });
  submit(h.render(googleProps)); await tick();
  assert.equal(body.developer_token, "legacy-token");
  assert.equal(body.login_customer_id, "5551234567");
  assert.equal(h.state[1].developer_token, "");
});

test("optional Google Ads developer token does not bypass actual OAuth, account, currency or busy checks", async () => {
  for (const method of ["access", "oauth"]) {
    const required = ["customer_id", "currency", ...(method === "access" ? ["access_token"] : ["client_id", "client_secret", "refresh_token"])];
    for (const field of required) {
      const h = harness("app/connections/page.tsx", "GoogleAdsForm", [method, googleForm({ [field]: "" })], {
        requestJson: () => assert.fail("incomplete Google Ads setup must not save"),
      });
      const view = h.render(googleProps);
      assert.equal(saveButton(view).props.disabled, true, `${method}: ${field}`);
      submit(view); await tick();
    }
    const h = harness("app/connections/page.tsx", "GoogleAdsForm", [method, googleForm()], {
      requestJson: () => assert.fail("busy Google Ads form must not save"),
    });
    const view = h.render({ ...googleProps, busy: true });
    assert.equal(saveButton(view).props.disabled, true);
    submit(view); await tick();
  }
});

test("Google Ads optional legacy-token explanation is localized and accessible in every language", () => {
  for (const language of ["en", "zh-Hans", "zh-Hant"]) {
    const h = harness("app/connections/page.tsx", "GoogleAdsForm", ["oauth", googleForm()], {}, language);
    const view = h.render(googleProps);
    assert.ok(text(view).includes(h.messages["connections.googleDeveloperTokenHint"]));
    assert.match(h.messages["connections.googleDeveloperToken"], /optional|可选|選填/);
    assert.match(h.messages["connections.googleDeveloperTokenHint"], /2026/);
    assert.match(h.messages["connections.googleDeveloperTokenHint"], /OAuth/);
    assert.match(h.messages["connections.googleDeveloperTokenHint"], /Google Cloud/);
    const input = nodes(view).find((n) => n?.type === "input" && n.props.id === "google-developer_token");
    assert.equal(input.props.required, undefined);
    assert.equal(input.props["aria-describedby"], "google-developer_token-hint");
    assert.ok(nodes(view).some((n) => n?.props?.id === input.props["aria-describedby"]));
  }
});

test("new readiness, currency and auth recovery messages exist in all supported languages", () => {
  for (const language of ["en", "zh-Hans", "zh-Hant"]) {
    const messages = require(path.join(frontend, `lib/i18n/ui.${language}.json`));
    for (const key of ["auth.expiredTitle", "auth.expiredBody", "auth.reauth", "live.authRequired", "growth.live.missingSources", "growth.live.auditNotSaved", "connections.currency", "connections.refreshFailed", "connections.googleOAuthRefreshFailed", "connections.googleAccountAccessFailed", "connections.googleUserAccessFailed", "connections.googleScopeFailed", "connections.googleProjectAccessFailed"]) assert.ok(messages[key], `${language}: ${key}`);
  }
});

test("money formatting preserves minor units, fractional JPY MRR and missing values", () => {
  for (const [amount, currency, expected] of [[0.4, "USD", "0.40"], [12.34, "USD", "12.34"], [0.4, "JPY", "0.40"], [12.34, "JPY", "12.34"], [1, "JPY", "1"], [0.123, "KWD", "0.123"], [0.0001, "USD", "0.0001"]]) {
    const result = reporting.formatMoney(amount, currency, "en-US");
    assert.ok(result.includes(currency));
    assert.ok(result.includes(expected), result);
  }
  assert.equal(reporting.formatMoney(null, "USD", "en-US"), "—");
  assert.equal(reporting.formatMoney("", "USD", "en-US"), "—");
});

test("Growth renders per-source dates, verified zones, collection times and incomplete MRR in every language", () => {
  const period = { start: "2026-09-17", end: "2026-10-16" };
  const report = { ...fixture, period_policy: "common_complete_calendar_days",
    revenue: { ...fixture.revenue, revenue: "0.40", mrr: null, mrr_status: "incomplete", mrr_policy: "net_recurring_v1", mrr_warnings: ["mrr_temporary_discount_unsupported"], mrr_as_of: "2026-10-18T00:00:00Z", period, reporting_timezone: "UTC", collected_at: "2026-10-18T00:00:00Z" },
    google_ads: { ...fixture.google_ads, period, reporting_timezone: "America/Los_Angeles", collected_at: "2026-10-18T00:01:00Z" },
    meta_ads: { ...fixture.meta_ads, period, reporting_timezone: "Asia/Tokyo", collected_at: "2026-10-18T00:02:00Z" },
  };
  for (const lang of ["en", "zh-Hans", "zh-Hant"]) {
    const h = harness("app/dashboard/page.tsx", "LiveGrowth", readyState(report), {}, lang);
    const rendered = text(h.render());
    for (const expected of ["2026-09-17", "2026-10-16", "America/Los_Angeles", "Asia/Tokyo", "UTC", "0.40", h.messages["growth.live.mrrIncomplete"], h.messages["growth.live.mrrWarningTemporaryDiscount"], h.messages["growth.live.mrrSnapshot"]]) assert.ok(rendered.includes(expected), `${lang}: ${expected}`);
    assert.doesNotMatch(rendered, /mrr_temporary_discount_unsupported/);
    const metric = nodes(h.render()).find((n) => n?.props?.className === "metric" && text(n).includes(h.messages["growth.live.revenue"]));
    assert.equal(nodes(metric).find((n) => n?.type === "dd").props.children, "—");
  }
});

test("Connections exposes current identity but masks workspace and separates storage state from session verification", () => {
  for (const lang of ["en", "zh-Hans", "zh-Hant"]) {
    const page = harness("app/connections/page.tsx", "ConnectionsPage", [{ tenant_id: "0123456789abcdef0123456789abcdef", connections: [] }, null], {}, lang);
    const pageText = text(page.render());
    assert.ok(pageText.includes("judge@example.test"));
    assert.ok(pageText.includes("012345…cdef"));
    assert.ok(!pageText.includes("0123456789abcdef0123456789abcdef"));
    assert.ok(pageText.includes(page.messages["connections.savedNotVerified"]));
    for (const storage_status of ["saved", "missing", "expired", "unavailable", "server_environment", "unknown"]) {
      const h = harness("app/connections/page.tsx", "ConnectionRow", [], {}, lang);
      const rendered = text(h.render({ item: { ...stripe, storage_status, saved_at: "2026-10-17T00:00:00Z", expires_at: "2026-10-24T00:00:00Z" }, onChanged: async () => {} }));
      assert.ok(rendered.includes(h.messages[`connections.storage.${storage_status}`]));
      assert.ok(rendered.includes(h.messages["connections.notChecked"]));
      assert.ok(rendered.includes("UTC"));
    }
  }
});

test("AI decision evidence uses the same synthetic case as Today and Growth", () => {
  const sample = harness("app/builder-cup/page.tsx", "SAMPLE_EVIDENCE").value;
  const business = JSON.parse(fs.readFileSync(path.join(frontend, "../data/demo_phase2_slowing_growth.json"), "utf8"));
  const founder = JSON.parse(fs.readFileSync(path.join(frontend, "../data/demo_phase3_founder_experience.json"), "utf8"));
  const totalSpend = Number(business.google_ads.spend) + Number(business.meta_ads.spend);
  const totalConversions = Number(business.google_ads.conversions) + Number(business.meta_ads.conversions);
  const visibility = 100 * founder.visibility_observations.filter((v) => v.cited_brands.includes(business.request.company.company_name)).length / founder.visibility_observations.length;
  for (const lang of ["en", "zh-Hans", "zh-Hant"]) {
    assert.ok(sample[lang][0].value.includes(Number(business.revenue.mrr).toLocaleString("en-US")));
    assert.ok(sample[lang][1].value.includes(String(totalSpend / totalConversions)));
    assert.ok(sample[lang][2].value.includes(business.google_ads.roas));
    assert.ok(sample[lang][3].value.includes(`${visibility}/100`));
    assert.ok(sample[lang].every((e) => e.observed_at.startsWith(business.request.period.end)));
    assert.ok(!JSON.stringify(sample[lang]).includes("31/100"));
  }
  assert.equal(founder.revenue_change_pct, undefined, "no second, unrelated revenue trend fixture");
});

test("MRR warning mappings and metadata copy are complete in all languages", () => {
  const warnings = harness("app/dashboard/page.tsx", "MRR_WARNING_KEYS").value;
  const en = require(path.join(frontend, "lib/i18n/ui.en.json"));
  for (const lang of ["en", "zh-Hans", "zh-Hant"]) {
    const messages = require(path.join(frontend, `lib/i18n/ui.${lang}.json`));
    assert.deepEqual(Object.keys(messages).sort(), Object.keys(en).sort());
    for (const key of Object.values(warnings)) assert.ok(messages[key], `${lang}: ${key}`);
  }
});

test("a checked connection has a timestamp only for this session", () => {
  const state = rowState();
  state[5] = { success: true, checked_live: true, message: "safe", checked_at: "2026-10-18T00:03:00Z" };
  const h = harness("app/connections/page.tsx", "ConnectionRow", state);
  const rendered = text(h.render({ item: { ...stripe, configured: true, storage_status: "saved" }, onChanged: async () => {} }));
  assert.ok(rendered.includes(h.messages["connections.testPassed"]));
  assert.ok(rendered.includes("12:03 AM UTC"));
  assert.ok(!rendered.includes(h.messages["connections.notChecked"]));
});

test("real API test environments are never presented as production results in any language", () => {
  for (const lang of ["en", "zh-Hans", "zh-Hant"]) {
    const report = { ...fixture, revenue: { ...fixture.revenue, account_environment: "test" }, google_ads: { ...fixture.google_ads, account_environment: "test" } };
    const h = harness("app/dashboard/page.tsx", "LiveGrowth", readyState(report), {}, lang);
    const rendered = text(h.render());
    assert.ok(rendered.includes(h.messages["environment.testReport"]));
    assert.ok(rendered.includes(h.messages["environment.test"]));
    assert.ok(rendered.includes(h.messages["environment.unknown"]));
    assert.ok(!rendered.includes(h.messages["environment.live"]));
    for (const environment of ["test", "live", "unknown"]) {
      const row = harness("app/connections/page.tsx", "ConnectionRow", [], {}, lang);
      assert.ok(text(row.render({ item: { ...stripe, account_environment: environment }, onChanged: async () => {} })).includes(row.messages[`environment.${environment}`]));
    }
    const state = rowState();
    state[5] = { success: true, checked_live: true, message: "safe", account_environment: "test" };
    const checked = harness("app/connections/page.tsx", "ConnectionRow", state, {}, lang);
    assert.ok(text(checked.render({ item: { ...stripe, provider: "google_ads", configured: true, account_environment: "unknown" }, onChanged: async () => {} })).includes(checked.messages["environment.test"]));
  }
});

test("timezone verification failures provide safe actionable guidance", async () => {
  const row = harness("app/connections/page.tsx", "ConnectionRow", rowState(), { requestJson: async () => { throw new ApiError("Google Ads reporting timezone cannot be verified", 502); } });
  submit(row.render({ item: stripe, onChanged: async () => {} })); await tick();
  assert.equal(row.state[4], row.messages["growth.live.timezoneUnavailable"]);
  const state = rowState(); state[5] = { success: false, checked_live: true, message: "Account reporting timezone is unavailable" };
  const checked = harness("app/connections/page.tsx", "ConnectionRow", state);
  assert.ok(text(checked.render({ item: stripe, onChanged: async () => {} })).includes(checked.messages["growth.live.timezoneUnavailable"]));
});
