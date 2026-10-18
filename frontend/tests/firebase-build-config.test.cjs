const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

const frontend = path.resolve(__dirname, "..");
const fixture = {
  NEXT_PUBLIC_FIREBASE_API_KEY: "ci-placeholder-not-a-real-api-key",
  NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "growthos-ci.invalid",
  NEXT_PUBLIC_FIREBASE_PROJECT_ID: "growthos-ci-fixture",
  NEXT_PUBLIC_FIREBASE_APP_ID: "1:000000000000:web:ci-fixture",
};
const source = ts.transpileModule(fs.readFileSync(path.join(frontend, "next.config.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function loadConfig(env) {
  const exports = {};
  // Isolate from local .env files and the developer's shell configuration.
  vm.runInNewContext(source, { exports, process: { env } });
  return exports.default;
}

test("production build refuses every missing or blank public Firebase identifier", () => {
  assert.throws(() => loadConfig({ NODE_ENV: "production" }), /requires Firebase public configuration/);
  for (const key of Object.keys(fixture)) {
    for (const value of [undefined, "", " \t "]) {
      assert.throws(
        () => loadConfig({ NODE_ENV: "production", ...fixture, [key]: value }),
        (error) => error.message.includes(key),
        `${key}: ${JSON.stringify(value)}`,
      );
    }
  }
});

test("complete public Firebase configuration preserves static Hosting export", () => {
  const config = loadConfig({ NODE_ENV: "production", ...fixture });
  assert.equal(config.output, "export");
  assert.equal(config.trailingSlash, true);
});

test("development and test config can load without production Firebase identifiers", () => {
  for (const NODE_ENV of ["development", "test"]) {
    assert.equal(loadConfig({ NODE_ENV }).output, "export");
  }
});

test("clean CI build explicitly supplies synthetic public Firebase config", () => {
  const workflow = fs.readFileSync(path.join(frontend, "../.github/workflows/ci.yml"), "utf8");
  const build = workflow.match(/- run: npm run build\n([\s\S]*?)(?=\n      - run:|$)/)?.[1];
  assert.ok(build, "CI must retain a production build check");
  const env = Object.fromEntries(
    [...build.matchAll(/^\s+(NEXT_PUBLIC_FIREBASE_[A-Z_]+):\s*"([^"]+)"\s*$/gm)]
      .map((match) => [match[1], match[2]]),
  );
  assert.deepEqual(env, fixture, "CI uses public placeholders, never deployment credentials");
  assert.equal(loadConfig({ NODE_ENV: "production", ...env }).output, "export");
});
