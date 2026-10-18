const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

const frontend = path.resolve(__dirname, "..");

function loadAuth(auth, signIn, options = {}) {
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(path.join(frontend, "lib/firebase.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(source, {
    exports,
    window: {},
    setTimeout: options.setTimeout ?? setTimeout,
    clearTimeout: options.clearTimeout ?? clearTimeout,
    process: { env: {
      NEXT_PUBLIC_FIREBASE_API_KEY: "test-key",
      NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "test.invalid",
      NEXT_PUBLIC_FIREBASE_PROJECT_ID: "test-project",
      NEXT_PUBLIC_FIREBASE_APP_ID: "test-app",
    } },
    require(name) {
      if (name === "@firebase/app") return { getApps: () => [{}], getApp: () => ({}) };
      if (name === "@firebase/auth") return {
        getAuth: () => auth, signInAnonymously: signIn,
        GoogleAuthProvider: class { setCustomParameters() {} },
        signInWithRedirect: () => assert.fail("redirect not expected"),
        ...options.firebase,
      };
      throw new Error(`Unexpected import: ${name}`);
    },
  });
  return Object.assign(exports.getFirebaseIdToken, exports);
}

test("requests obtain the current token instead of retaining an expired first token", async () => {
  let token = "first-token";
  const refreshes = [];
  const auth = {
    authStateReady: async () => {},
    currentUser: { getIdToken: async (force) => { refreshes.push(force); return token; } },
  };
  const getToken = loadAuth(auth, () => assert.fail("restored user must not sign in again"));
  assert.equal(await getToken(), "first-token");
  token = "refreshed-token";
  assert.equal(await getToken(), "refreshed-token");
  assert.equal(await getToken(true), "refreshed-token");
  assert.deepEqual(refreshes, [false, false, true]);
});

test("parallel first requests share one anonymous sign-in and retry after failure", async () => {
  let attempts = 0;
  let complete;
  const auth = { currentUser: null, authStateReady: async () => {} };
  const user = { getIdToken: async () => "signed-in-token" };
  const getToken = loadAuth(auth, () => {
    attempts += 1;
    if (attempts === 1) return Promise.reject(new Error("temporary login failure"));
    return new Promise((resolve) => { complete = () => { auth.currentUser = user; resolve({ user }); }; });
  });
  await assert.rejects(getToken(), /temporary login failure/);
  const first = getToken();
  const second = getToken();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(attempts, 2);
  complete();
  assert.deepEqual(await Promise.all([first, second]), ["signed-in-token", "signed-in-token"]);
});

test("restored anonymous identity is reused before a new sign-in can start", async () => {
  const auth = {
    currentUser: null,
    authStateReady: async () => { auth.currentUser = { getIdToken: async () => "restored-token" }; },
  };
  const getToken = loadAuth(auth, () => assert.fail("identity was already restored"));
  assert.equal(await getToken(), "restored-token");
});

test("Live credentials reject anonymous or missing users without creating an anonymous session", async () => {
  for (const user of [null, { uid: "anonymous", isAnonymous: true }]) {
    const auth = { currentUser: user, authStateReady: async () => {} };
    const getToken = loadAuth(auth, () => assert.fail("Live must not create an anonymous identity"));
    await assert.rejects(getToken(false, true), /registered-user-required/);
  }
});

test("Live token comes from the formal user and is discarded if identity changes mid-refresh", async () => {
  const auth = { authStateReady: async () => {}, currentUser: null };
  const user = { uid: "alice", isAnonymous: false, getIdToken: async () => "alice-token" };
  auth.currentUser = user;
  const getToken = loadAuth(auth, () => assert.fail("formal identity already exists"));
  assert.equal(await getToken(false, true), "alice-token");
  user.getIdToken = async () => { auth.currentUser = { uid: "bob" }; return "alice-token"; };
  await assert.rejects(getToken(true, true), /identity-changing/);
});

test("Google popup timeout preserves the page and unlocks retry without forcing redirect", async () => {
  const timers = [];
  const popups = [];
  let redirects = 0;
  const auth = { currentUser: { uid: "anonymous", isAnonymous: true, getIdToken: async () => "anon-token" }, authStateReady: async () => {} };
  const module = loadAuth(auth, () => assert.fail("no anonymous sign-in needed"), {
    setTimeout: (callback, delay) => { timers.push({ callback, delay }); return timers.length; },
    clearTimeout: () => {},
    firebase: {
      signInWithPopup: () => new Promise((resolve) => popups.push(resolve)),
      signInWithRedirect: async () => { redirects += 1; },
    },
  });
  const first = module.signInWithGoogle();
  assert.equal(popups.length, 1, "popup must open synchronously before any await");
  assert.equal(timers[0].delay, 45000);
  await assert.rejects(module(), /identity-changing/);
  timers[0].callback();
  await assert.rejects(first, /popup-timeout/);
  assert.equal(redirects, 0, "slow account selection or MFA must not force navigation");
  assert.equal(await module(), "anon-token", "a timed-out popup must not hold the identity lock forever");
  const retry = module.signInWithGoogle();
  assert.equal(popups.length, 2);
  popups[0]({ user: auth.currentUser });
  await Promise.resolve();
  await assert.rejects(module(), /identity-changing/, "late first popup must not unlock the retry");
  popups[1]({ user: auth.currentUser });
  await retry;
  assert.equal(await module(), "anon-token");
});

test("explicitly blocked or unsupported Google popups still use redirect fallback", async () => {
  for (const code of ["auth/popup-blocked", "auth/operation-not-supported-in-this-environment", "auth/web-storage-unsupported"]) {
    let redirects = 0;
    const auth = { currentUser: { uid: "anonymous", isAnonymous: true }, authStateReady: async () => {} };
    const module = loadAuth(auth, () => assert.fail("no anonymous sign-in needed"), {
      firebase: {
        signInWithPopup: async () => { throw Object.assign(new Error(code), { code }); },
        signInWithRedirect: async (selectedAuth) => { assert.equal(selectedAuth, auth); redirects += 1; },
      },
    });
    await module.signInWithGoogle();
    assert.equal(redirects, 1, code);
  }
});

test("Google popup cancellation remains a user-visible error and does not force redirect", async () => {
  let redirects = 0;
  const auth = { currentUser: { uid: "anonymous", isAnonymous: true }, authStateReady: async () => {} };
  const module = loadAuth(auth, () => assert.fail("no anonymous sign-in needed"), {
    firebase: {
      signInWithPopup: async () => { throw Object.assign(new Error("closed"), { code: "auth/popup-closed-by-user" }); },
      signInWithRedirect: async () => { redirects += 1; },
    },
  });
  await assert.rejects(module.signInWithGoogle(), /closed/);
  assert.equal(redirects, 0);
});

test("pending interactive authentication keeps visible sign-in guidance instead of a loading-only screen", () => {
  const React = require("react");
  const { renderToStaticMarkup } = require("react-dom/server");
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(path.join(frontend, "components/AppShell.tsx"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(source, {
    exports,
    require(name) {
      if (name === "react" || name === "react/jsx-runtime") return require(name);
      if (name === "next/link") return { default: ({ children, ...props }) => React.createElement("a", props, children) };
      if (name === "next/navigation") return { usePathname: () => "/" };
      if (name === "../lib/i18n") return { useI18n: () => ({ t: (key) => key }) };
      if (name === "./DataModeContext") return { WorkspaceModeSwitch: () => null, useDataMode: () => ({
        mode: "live", capabilities: { live_self_service_enabled: true }, session: null,
        sessionLoading: true, authPending: true, identity: null, identityKey: "guest", logout: async () => {},
      }) };
      if (name === "./LanguageSwitch") return { LanguageSwitch: () => null };
      if (name === "./LiveSignIn") return { LiveSignIn: () => React.createElement("div", null, "visible-sign-in-guidance") };
      if (name === "./PageState") return { PageState: () => React.createElement("div", null, "loading-only") };
      throw new Error(`Unexpected import: ${name}`);
    },
  });
  const html = renderToStaticMarkup(React.createElement(exports.AppShell, null, "private-account-data"));
  assert.match(html, /visible-sign-in-guidance/);
  assert.doesNotMatch(html, /loading-only|private-account-data/);
});

function loadApi(fetchResponse, options = {}) {
  const exports = {};
  const tokenRequests = [];
  const registeredRequests = [];
  const requests = [];
  let identity = "alice:1";
  const source = ts.transpileModule(fs.readFileSync(path.join(frontend, "lib/api.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(source, {
    exports,
    process: { env: {} },
    require(name) {
      assert.equal(name, "./firebase");
      return { firebaseIdentityKey: () => identity, getFirebaseIdToken: async (force = false, registered = false) => {
        tokenRequests.push(force);
        registeredRequests.push(registered);
        if (options.token) return options.token({ force, registered, setIdentity: (next) => { identity = next; } });
        return force ? "fresh-token" : "old-token";
      } };
    },
    fetch: async (url, init) => {
      requests.push({ url, ...init });
      return fetchResponse(requests.length, (next) => { identity = next; });
    },
  });
  return { ...exports, requests, tokenRequests, registeredRequests };
}

test("API retries a rejected identity once with a forced fresh token and the original body", async () => {
  const api = loadApi((attempt) => attempt === 1
    ? Response.json({ detail: "Expired identity" }, { status: 401 })
    : Response.json({ approval_status: "executed" }));
  const body = JSON.stringify({ outcome: "approve", action_digest: "test-digest" });
  const result = await api.requestJson("/api/v1/competition/workflows/test/decision", { method: "POST", body });
  assert.equal(result.approval_status, "executed");
  assert.deepEqual(api.tokenRequests, [false, true]);
  assert.equal(api.requests.length, 2);
  assert.equal(api.requests[0].headers.Authorization, "Bearer old-token");
  assert.equal(api.requests[1].headers.Authorization, "Bearer fresh-token");
  assert.equal(api.requests[1].body, body);
  assert.equal(api.requests[1].method, "POST");
});

test("anonymous and unverified /me responses accept a null workspace as a normal signed-out state", () => {
  const { isWorkspaceSession } = loadApi(() => assert.fail("validation must not fetch"));
  assert.equal(isWorkspaceSession({ uid: "anonymous-user", workspace_id: null, authenticated: false, live_allowed: false }), true);
  assert.equal(isWorkspaceSession({ uid: "unverified-user", workspace_id: null, authenticated: false, live_allowed: false }), true);
  assert.equal(isWorkspaceSession({ uid: "registered-user", workspace_id: null, authenticated: true, live_allowed: false }), true);
});

test("Live /me responses require a nonempty workspace and authenticated session", () => {
  const { isWorkspaceSession } = loadApi(() => assert.fail("validation must not fetch"));
  for (const uid of [null, "verified-user"]) {
    assert.equal(isWorkspaceSession({ uid, workspace_id: "local-demo", authenticated: true, live_allowed: true }), true);
  }
  for (const workspace_id of [null, "", " ", undefined, 123]) {
    assert.equal(isWorkspaceSession({ uid: "verified-user", workspace_id, authenticated: true, live_allowed: true }), false);
  }
  assert.equal(isWorkspaceSession({ uid: "anonymous-user", workspace_id: "someone-else", authenticated: false, live_allowed: true }), false);
});

test("malformed /me responses remain fail-closed", () => {
  const { isWorkspaceSession } = loadApi(() => assert.fail("validation must not fetch"));
  for (const value of [null, {}, { uid: null, workspace_id: null, authenticated: "false", live_allowed: false },
    { uid: null, workspace_id: null, authenticated: false },
    { uid: "", workspace_id: null, authenticated: false, live_allowed: false }]) {
    assert.equal(isWorkspaceSession(value), false);
  }
});

test("API does not loop when the refreshed identity is also rejected", async () => {
  const api = loadApi(() => Response.json({ detail: "Invalid identity" }, { status: 401 }));
  await assert.rejects(api.requestJson("/api/v1/dashboard"), (error) => {
    assert.ok(error instanceof api.ApiError);
    assert.equal(error.status, 401);
    assert.equal(error.message, "Invalid identity");
    return true;
  });
  assert.equal(api.requests.length, 2);
  assert.deepEqual(api.tokenRequests, [false, true]);
});

test("API surfaces forbidden workspace access without retrying it", async () => {
  const api = loadApi(() => Response.json({ detail: "This workspace is unavailable" }, { status: 403 }));
  await assert.rejects(api.requestJson("/api/v1/connections"), (error) => {
    assert.equal(error.status, 403);
    assert.equal(error.message, "This workspace is unavailable");
    return true;
  });
  assert.equal(api.requests.length, 1);
  assert.deepEqual(api.tokenRequests, [false]);
});

test("API validation arrays become readable messages rather than object strings", async () => {
  const api = loadApi(() => Response.json({ detail: [
    { loc: ["body", "founder_goal"], msg: "A goal is required", type: "missing" },
    { loc: ["body", "evidence"], msg: "At least two facts are required", type: "too_short" },
  ] }, { status: 422 }));
  await assert.rejects(api.requestJson("/api/v1/competition/analyze", { method: "POST", body: "{}" }), (error) => {
    assert.ok(error instanceof api.ApiError);
    assert.equal(error.status, 422);
    assert.equal(error.message, "A goal is required; At least two facts are required");
    return true;
  });
  assert.equal(api.requests.length, 1);
});

test("all credential and Live reads require a registered user; Sample and /me remain anonymous-capable", async () => {
  const api = loadApi(() => Response.json({ ok: true }));
  for (const [pathname, body] of [
    ["/api/v1/connections", undefined],
    ["/api/v1/connections/stripe/credential", JSON.stringify({ secret: "test-only" })],
    ["/api/v1/growth-intelligence/live", undefined],
    ["/api/v1/audit-log?mode=live&limit=50", undefined],
    ["/api/v1/visibility/scan", JSON.stringify({ mode: "live" })],
    ["/api/v1/me", undefined],
    ["/api/v1/competition/analyze", JSON.stringify({ language: "en" })],
    ["/api/v1/dashboard?mode=demo", undefined],
  ]) await api.requestJson(pathname, { body });
  assert.deepEqual(api.registeredRequests, [true, true, true, true, true, false, false, false]);
});

test("identity changes prevent old credentials from being sent with a new user's token", async () => {
  const api = loadApi(() => assert.fail("no request may be sent"), {
    token: ({ setIdentity }) => { setIdentity("bob:2"); return "bob-token"; },
  });
  await assert.rejects(api.requestJson("/api/v1/connections/stripe/credential", {
    method: "PUT", body: JSON.stringify({ secret: "alice-key" }),
  }), /identity-changing/);
  assert.equal(api.requests.length, 0);
});

test("identity changes discard old responses and prevent replaying a mutation", async () => {
  for (const status of [200, 401]) {
    const api = loadApi((_attempt, setIdentity) => {
      setIdentity("bob:2");
      return Response.json({ secret_data: "alice-data" }, { status });
    });
    await assert.rejects(api.requestJson("/api/v1/connections/stripe/credential", {
      method: "PUT", body: JSON.stringify({ secret: "alice-key" }),
    }), /identity-changing/);
    assert.equal(api.requests.length, 1);
    assert.deepEqual(api.tokenRequests, [false]);
  }
});

test("identity changes during JSON decoding cannot display another user's data", async () => {
  const api = loadApi((_attempt, setIdentity) => ({
    status: 200, ok: true,
    json: async () => { setIdentity("bob:2"); return { account: "alice" }; },
  }));
  await assert.rejects(api.requestJson("/api/v1/connections"), /identity-changing/);
});

function loadWorker(fetchResponse) {
  const handlers = {};
  const saved = new Map();
  const cache = { put: async (request, response) => { saved.set(request.url ?? request, response); } };
  vm.runInNewContext(fs.readFileSync(path.join(frontend, "public/sw.js"), "utf8"), {
    URL,
    self: { location: { origin: "https://growthos.test" }, addEventListener: (name, handler) => { handlers[name] = handler; } },
    caches: { open: async () => cache, match: async (request) => saved.get(request.url ?? request) },
    fetch: async () => fetchResponse(),
  });
  return {
    saved,
    async request(pathname, mode = "cors") {
      let result;
      handlers.fetch({
        request: { url: `https://growthos.test${pathname}`, method: "GET", mode },
        respondWith(promise) { result = promise; },
      });
      return result;
    },
  };
}

test("worker caches successful scripts, but not HTML fallbacks or failed responses", async () => {
  for (const [status, type, shouldCache] of [[200, "application/javascript", true], [200, "text/html", false], [503, "application/javascript", false]]) {
    const worker = loadWorker(() => new Response("body", { status, headers: { "Content-Type": type } }));
    const response = await worker.request("/_next/static/chunk.js");
    assert.equal(response.status, status);
    assert.equal(worker.saved.size, shouldCache ? 1 : 0);
  }
});

test("failed navigation cannot overwrite the last usable offline page", async () => {
  let status = 200;
  const worker = loadWorker(() => new Response(status === 200 ? "usable page" : "unavailable", {
    status, headers: { "Content-Type": "text/html; charset=utf-8" },
  }));
  await worker.request("/builder-cup/", "navigate");
  status = 503;
  assert.equal((await worker.request("/builder-cup/", "navigate")).status, 503);
  assert.equal(await worker.saved.get("https://growthos.test/builder-cup/").text(), "usable page");
});

test("API responses always bypass the offline cache", async () => {
  const worker = loadWorker(() => assert.fail("API requests should use the browser network path"));
  assert.equal(await worker.request("/api/v1/capabilities", "navigate"), undefined);
  assert.equal(worker.saved.size, 0);
});
