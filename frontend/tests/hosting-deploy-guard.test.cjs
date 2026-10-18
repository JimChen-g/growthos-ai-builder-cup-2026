const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { verifyHostingBuild } = require("../../scripts/verify-hosting-build.cjs");

const project = "valid-release-project";
const config = {
  apiKey: "synthetic-public-key-for-unit-test",
  authDomain: `${project}.firebaseapp.com`,
  projectId: project,
  appId: "1:123456789:web:abcdef",
};
function artifact(t, values = config, client = values) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "growthos-hosting-guard-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.mkdirSync(path.join(dir, "_next/static"), { recursive: true });
  fs.writeFileSync(path.join(dir, "release-config.json"), JSON.stringify({ schema: 1, firebase: values }));
  fs.writeFileSync(path.join(dir, "_next/static/app.js"), `const config=${JSON.stringify(client)};`);
  return dir;
}

test("matching public build passes but a different or absent deployment target fails", (t) => {
  const dir = artifact(t);
  assert.equal(verifyHostingBuild(dir, project).verified, true);
  assert.throws(() => verifyHostingBuild(dir), /explicit target/);
  assert.throws(() => verifyHostingBuild(dir, "another-release-project"), /does not match/);
});

test("CI placeholders and reserved invalid auth domains cannot be deployed", (t) => {
  for (const [field, value] of [
    ["apiKey", "ci-placeholder-not-a-real-api-key"],
    ["projectId", "growthos-ci-fixture"],
    ["authDomain", "growthos-ci.invalid"],
    ["authDomain", "different.invalid"],
  ]) {
    assert.throws(() => verifyHostingBuild(artifact(t, { ...config, [field]: value }), project), /placeholder/);
  }
});

test("every missing or blank public identifier fails deployment", (t) => {
  for (const field of Object.keys(config)) {
    for (const value of [undefined, "", "  "]) {
      assert.throws(() => verifyHostingBuild(artifact(t, { ...config, [field]: value }), project), /schema|missing/);
    }
  }
});

test("editing the release manifest cannot approve a stale or placeholder JS build", (t) => {
  assert.throws(() => verifyHostingBuild(artifact(t, config, { ...config, apiKey: "ci-placeholder" }), project), /placeholder client/);
  assert.throws(() => verifyHostingBuild(artifact(t, config, { ...config, projectId: "other-project" }), project), /does not match its release metadata/);
});

test("missing release metadata and absent client configuration fail closed", (t) => {
  const dir = artifact(t, config, {});
  assert.throws(() => verifyHostingBuild(dir, project), /does not match its release metadata/);
  fs.unlinkSync(path.join(dir, "release-config.json"));
  assert.throws(() => verifyHostingBuild(dir, project), /configuration is missing/);
});

test("Firebase Hosting always invokes the artifact guard before deploy", () => {
  const firebase = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../firebase.json"), "utf8"));
  assert.deepEqual(firebase.hosting.predeploy, ['node "$PROJECT_DIR/scripts/verify-hosting-build.cjs"']);
});

test("unrelated framework invalid-domain sentinels are not mistaken for Firebase config", (t) => {
  const dir = artifact(t);
  fs.appendFileSync(path.join(dir, "_next/static/app.js"), 'const sentinel="https://example.invalid";');
  assert.equal(verifyHostingBuild(dir, project).verified, true);
});
