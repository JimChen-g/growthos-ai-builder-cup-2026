# AI Builder Cup implementation status

Snapshot date: 2026-10-17 (Asia/Shanghai)

Labels used below:

- **Verified locally**: implemented and exercised in this clean competition repository.
- **Deployed smoke verified**: the public path passed the explicitly described end-to-end check;
  unlisted recovery, concurrency or isolation cases are not implied.
- **Not run**: requires a deployed public version or human study.

| Item | Status | Evidence / remaining acceptance |
|---|---|---|
| DEV-01 | Verified locally | Pinned source `2a2bd210`; allowlisted export; new Git history; original working tree unchanged. |
| DEV-02 | Deployed smoke verified | Billing-linked project, `us-central1`, enabled services, dedicated runtime identity and minimum runtime IAM were accepted during deployment. |
| DEV-03 | Deployed smoke verified | A structured `gemini-3.7-flash` Vertex response passed server-side evidence validation through the public path. |
| DEV-04 | Verified locally | Strict structured contract, stable evidence IDs, server-side citation validation, exactly two actions and labelled fallback tests. |
| DEV-05 | Deployed smoke verified | The bounded endpoint returned a live Vertex result and the only executable judge action recorded a simulation. |
| DEV-06 | Partly deployed verified | Lazy anonymous sign-in and server-side token verification passed in one real browser session; two-session isolation remains. |
| DEV-07 | Partly deployed verified | Firestore created and stored the smoke-test workflow/approval; recovery across a fresh revision remains. |
| DEV-08 | Verified locally; cloud concurrency pending | Transactional approval claim, idempotent repeat handling and session isolation are tested against the store contract. Firestore multi-instance acceptance remains. |
| DEV-09 | Partly deployed verified | Cache, quotas and bounds are tested; TTL is enabled for cache, workflow and quota collection groups. Live quota exhaustion remains. |
| DEV-10 | Deployed smoke verified | Hosting returned a labelled live Vertex result within the bounded request window. |
| DEV-11 | Deployed smoke verified | Dedicated runtime account has only Vertex user and Datastore user roles; build context and browser-token scans passed. |
| DEV-12 | Deployed smoke verified | Ten static routes were exported and `/builder-cup/` plus its same-origin `/api/**` rewrite passed. |
| DEV-13 | Deployed smoke verified | Cloud Run revision `growthos-builder-cup-api-00001-rjk` serves 100% traffic; public `/health` passed. |
| DEV-14 | Deployed smoke verified | Hosting, Cloud Run, Firestore, Anonymous Auth and Vertex completed one public analysis and simulated approval. |
| DEV-15 | Verified locally | English, Simplified Chinese and Traditional Chinese judge path with sample/live/cache/fallback/simulated labels. |
| DEV-16 | Partly deployed verified | 115 backend tests pass and deployed auth/analysis/approval passed; recovery and concurrency remain. |
| DEV-17 | Not run | No efficiency percentages or productivity claims are reported. Requires deployed version and three paired repetitions per task. |
| DEV-18 | In progress | Public URL, cloud revision and deployed smoke acceptance are recorded; deck/video/submission copy remains. |

## Verified commands

- `python -m ruff check .`: passed.
- `python -m pytest -q`: 115 passed.
- `npm ci`, `npm run typecheck`, `npm run build`: passed; ten static routes exported.
- `npm audit --omit=dev --audit-level=high`: zero known vulnerabilities at the configured threshold.
- Local Docker build/start/health request: passed.

The deployed smoke test proves the specific Firebase Auth, Hosting, Cloud Run, Firestore and Vertex
path recorded above. It does not prove the remaining multi-session, recovery, concurrency, quota or
failure-mode cases.
