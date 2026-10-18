const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

test("all decision-demo languages use the same MRR snapshot, not invented daily/monthly growth", () => {
  const root = path.resolve(__dirname, "../..");
  const fixture = JSON.parse(fs.readFileSync(path.join(root, "data/demo_phase2_slowing_growth.json"), "utf8"));
  const page = fs.readFileSync(path.join(root, "frontend/app/builder-cup/page.tsx"), "utf8");
  const block = page.match(/const EVIDENCE_TEXT:[\s\S]*?(?=\nconst EVIDENCE_IDS)/)?.[0];
  assert.ok(block);
  const compiled = ts.transpileModule(`${block}\nexports.evidence = EVIDENCE_TEXT;`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports });
  assert.equal(Number(fixture.revenue.mrr), 30000);
  const explanations = { en: "no prior MRR comparison", "zh-Hans": "未提供上期 MRR 对比", "zh-Hant": "未提供上期 MRR 比較" };
  for (const [language, rows] of Object.entries(exports.evidence)) {
    assert.ok(rows[0][1].includes("30,000"));
    assert.ok(rows[0][1].includes(explanations[language]));
    assert.doesNotMatch(rows[0][1], /%|yesterday|昨日/);
  }
});
