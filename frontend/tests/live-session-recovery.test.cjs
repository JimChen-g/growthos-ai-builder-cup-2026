const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const root = path.resolve(__dirname, "..");

function compiled(file) {
  return ts.transpileModule(fs.readFileSync(path.join(root, file), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
}

function apiWith(statuses, tokenFailure) {
  const exports = {}, events = [], refreshes = [];
  let identity = "alice:1", calls = 0;
  vm.runInNewContext(compiled("lib/api.ts"), {
    exports, Event, Error, process: { env: {} },
    window: { dispatchEvent: (event) => events.push(event.type) },
    require: () => ({
      firebaseIdentityKey: () => identity,
      getFirebaseIdToken: async (force) => {
        refreshes.push(force);
        if (tokenFailure && force) throw Object.assign(new Error("expired"), { code: tokenFailure });
        return "test-token";
      },
    }),
    fetch: async () => {
      const result = statuses[calls++];
      if (result.changeIdentity) identity = "bob:2";
      return Response.json({ detail: "safe error" }, { status: result.status });
    },
  });
  return { ...exports, events, refreshes, calls: () => calls };
}

test("persistent private 401 retries once then invalidates the Live session", async () => {
  const api = apiWith([{ status: 401 }, { status: 401 }]);
  await assert.rejects(api.requestJson("/api/v1/connections"), { status: 401 });
  assert.deepEqual(api.refreshes, [false, true]);
  assert.deepEqual(api.events, [api.AUTH_SESSION_INVALID_EVENT]);
});

test("private 403 invalidates without retry; public /me never loops into invalidation", async () => {
  for (const [url, expected] of [["/api/v1/connections", 1], ["/api/v1/me", 0]]) {
    const api = apiWith([{ status: 403 }]);
    await assert.rejects(api.requestJson(url), { status: 403 });
    assert.equal(api.calls(), 1);
    assert.equal(api.events.length, expected);
  }
});

test("recovered token, provider failures and stale identity cannot invalidate a current session", async () => {
  const recovered = apiWith([{ status: 401 }, { status: 200 }]);
  await recovered.requestJson("/api/v1/connections");
  assert.deepEqual(recovered.events, []);
  const provider = apiWith([{ status: 502 }]);
  await assert.rejects(provider.requestJson("/api/v1/growth-intelligence/live"));
  assert.deepEqual(provider.events, []);
  const stale = apiWith([{ status: 403, changeIdentity: true }]);
  await assert.rejects(stale.requestJson("/api/v1/connections"), /identity-changing/);
  assert.deepEqual(stale.events, []);
});

test("revoked Firebase refresh tokens invalidate Live but network errors do not", async () => {
  for (const [code, expected] of [["auth/user-token-expired", 1], ["auth/network-request-failed", 0]]) {
    const api = apiWith([{ status: 401 }], code);
    await assert.rejects(api.requestJson("/api/v1/connections"));
    assert.equal(api.events.length, expected);
    assert.equal(api.calls(), 1);
  }
});

test("expired sessions show clear reauthentication instead of a generic error", () => {
  const exports = {};
  vm.runInNewContext(compiled("components/LiveSignIn.tsx"), {
    exports,
    require: (name) => {
      if (name === "react/jsx-runtime") return require(name);
      if (name === "../lib/i18n") return { useI18n: () => ({ t: (key) => key }) };
      if (name === "./DataModeContext") return { useDataMode: () => ({
        identity: { anonymous: false }, sessionExpired: true, sessionError: true,
        sessionLoading: false, authPending: false, login: async () => {}, retrySession: () => {},
      }) };
      throw new Error(`Unexpected import ${name}`);
    },
  });
  const html = renderToStaticMarkup(React.createElement(exports.LiveSignIn));
  assert.match(html, /auth.expiredTitle/);
  assert.match(html, /auth.expiredBody/);
  assert.match(html, /auth.reauth/);
  assert.doesNotMatch(html, /auth.error|auth.switchAccount/);
});
