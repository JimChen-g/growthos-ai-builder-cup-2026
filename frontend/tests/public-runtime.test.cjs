const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

const frontend = path.resolve(__dirname, "..");

function loadAuth(auth, signIn) {
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(path.join(frontend, "lib/firebase.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(source, {
    exports,
    window: {},
    process: { env: {
      NEXT_PUBLIC_FIREBASE_API_KEY: "test-key",
      NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "test.invalid",
      NEXT_PUBLIC_FIREBASE_PROJECT_ID: "test-project",
      NEXT_PUBLIC_FIREBASE_APP_ID: "test-app",
    } },
    require(name) {
      if (name === "@firebase/app") return { getApps: () => [{}], getApp: () => ({}) };
      if (name === "@firebase/auth") return { getAuth: () => auth, signInAnonymously: signIn };
      throw new Error(`Unexpected import: ${name}`);
    },
  });
  return exports.getFirebaseIdToken;
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

function loadApi(fetchResponse) {
  const exports = {};
  const tokenRequests = [];
  const requests = [];
  const source = ts.transpileModule(fs.readFileSync(path.join(frontend, "lib/api.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(source, {
    exports,
    process: { env: {} },
    require(name) {
      assert.equal(name, "./firebase");
      return { getFirebaseIdToken: async (force = false) => {
        tokenRequests.push(force);
        return force ? "fresh-token" : "old-token";
      } };
    },
    fetch: async (url, init) => {
      requests.push({ url, ...init });
      return fetchResponse(requests.length);
    },
  });
  return { ...exports, requests, tokenRequests };
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
