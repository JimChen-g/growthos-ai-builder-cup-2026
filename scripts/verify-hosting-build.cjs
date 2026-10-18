"use strict";

// Fail closed on the *built artifact*, not the publisher's current environment.
// CI may build synthetic config, but the same artifact must never be deployed.
const fs = require("node:fs");
const path = require("node:path");
const FIELDS = ["apiKey", "authDomain", "projectId", "appId"];
const PLACEHOLDER = /ci-placeholder|growthos-ci-|(?:[a-z0-9-]+\.)+invalid\b/i;
const CI_MARKERS = /ci-placeholder|growthos-ci-/i;

function verifyHostingBuild(outDir, project) {
  if (!project || !/^[a-z][a-z0-9-]{4,61}[a-z0-9]$/.test(project)) {
    throw new Error("Hosting deployment requires the explicit target project (GCLOUD_PROJECT).");
  }
  const metadataPath = path.join(outDir, "release-config.json");
  if (!fs.existsSync(metadataPath)) throw new Error("Rebuild: public Firebase release configuration is missing.");
  const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
  const config = metadata.firebase;
  if (metadata.schema !== 1 || !config || Object.keys(config).sort().join() !== [...FIELDS].sort().join()) {
    throw new Error("Invalid Firebase release configuration schema.");
  }
  for (const field of FIELDS) {
    if (typeof config[field] !== "string" || !config[field].trim() || PLACEHOLDER.test(config[field])) {
      throw new Error(`Hosting refuses missing or placeholder Firebase configuration: ${field}`);
    }
  }
  if (config.projectId !== project || config.authDomain !== `${project}.firebaseapp.com`) {
    throw new Error("Built Firebase project/auth domain does not match the Hosting deployment target.");
  }
  const chunks = [];
  function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const filename = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) throw new Error("Hosting build must not contain symbolic links.");
      if (entry.isDirectory()) walk(filename);
      else if (entry.isFile() && entry.name.endsWith(".js")) chunks.push(fs.readFileSync(filename, "utf8"));
    }
  }
  walk(path.join(outDir, "_next", "static"));
  // Framework/SDK code legitimately contains generic *.invalid sentinel URLs;
  // reject those only in Firebase config, not unrelated library literals.
  if (!chunks.length || chunks.some((chunk) => CI_MARKERS.test(chunk))) {
    throw new Error("Hosting refuses missing JavaScript or a CI/placeholder client artifact.");
  }
  // A modified manifest must not make a stale or differently configured build pass.
  if (!chunks.some((chunk) => FIELDS.every((field) => chunk.includes(JSON.stringify(config[field]))))) {
    throw new Error("Built client Firebase configuration does not match its release metadata; rebuild.");
  }
  return { verified: true, firebase_fields: FIELDS.length, client_chunks: chunks.length };
}

module.exports = { verifyHostingBuild };
if (require.main === module) {
  try {
    console.log(JSON.stringify(verifyHostingBuild(
      path.resolve(__dirname, "../frontend/out"), process.argv[2] || process.env.GCLOUD_PROJECT,
    )));
  } catch (error) {
    // No raw configuration values (including the public API key) in logs.
    console.error(error instanceof SyntaxError ? "Invalid Firebase release JSON." : error.message);
    process.exitCode = 1;
  }
}
