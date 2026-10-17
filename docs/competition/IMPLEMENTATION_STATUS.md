# AI Builder Cup implementation status

Snapshot date: 2026-10-17 (Asia/Shanghai)

Labels used below:

- **Verified locally**: implemented and exercised in this clean competition repository.
- **Implemented, cloud acceptance pending**: code/config exists, but the target GCP project has
  not been authenticated and no live-cloud claim is made.
- **Not run**: requires a deployed public version or human study.

| Item | Status | Evidence / remaining acceptance |
|---|---|---|
| DEV-01 | Verified locally | Pinned source `2a2bd210`; allowlisted export; new Git history; original working tree unchanged. |
| DEV-02 | Partly verified; cloud acceptance pending | Owner console access, project identity and a linked billing account were verified read-only. Region, quotas, deployer/runtime IAM and CLI owner authentication remain unresolved. |
| DEV-03 | Implemented, cloud acceptance pending | Agent Platform API is enabled and model/region/timeout are configurable; candidate is `gemini-3.7-flash`; a structured call in the target project is still required. |
| DEV-04 | Verified locally | Strict structured contract, stable evidence IDs, server-side citation validation, exactly two actions and labelled fallback tests. |
| DEV-05 | Verified locally; live call pending | Competition endpoint permits only the bounded analysis path and simulated action; existing real integrations are not exposed by the judge flow. |
| DEV-06 | Implemented, cloud acceptance pending | Lazy anonymous sign-in and server-side Firebase token verification are implemented; two real browser sessions still need deployed acceptance. |
| DEV-07 | Implemented, cloud acceptance pending | Firestore and in-memory adapters exist; workflow/cache/quota documents have retention timestamps. Live Firestore recovery remains unverified. |
| DEV-08 | Verified locally; cloud concurrency pending | Transactional approval claim, idempotent repeat handling and session isolation are tested against the store contract. Firestore multi-instance acceptance remains. |
| DEV-09 | Verified locally; cloud persistence pending | Cache, per-session/global/day quotas, size bounds and retention fields are tested. Firestore TTL policy must be enabled in the project. |
| DEV-10 | Verified locally; Hosting timing pending | One bounded Vertex request, 40-second timeout, two transient attempts and explicit source labels. End-to-end Hosting latency is unmeasured. |
| DEV-11 | Partly verified locally | Clean build context and browser-token scan are complete. Cloud Run API was not present in the read-only enabled-service inventory; runtime service account and IAM are not configured. |
| DEV-12 | Verified locally | Next.js static export produced ten routes, including `/builder-cup`; same-origin API calls and dynamic ID tokens replace localhost/shared-token defaults. |
| DEV-13 | Verified locally; cloud acceptance pending | Container builds and starts as a non-root user on `$PORT`; `/health` returned `{"status":"ok","phase":"5"}`. No Cloud Run revision exists yet. |
| DEV-14 | Not run | Requires authenticated deployment of Hosting, Cloud Run, Firestore, Auth and Vertex. |
| DEV-15 | Verified locally | English, Simplified Chinese and Traditional Chinese judge path with sample/live/cache/fallback/simulated labels. |
| DEV-16 | Partly verified locally | 115 backend tests pass; model failure, cache, quota, isolation and concurrent duplicate approval are covered. Deployed auth/recovery/concurrency remain. |
| DEV-17 | Not run | No efficiency percentages or productivity claims are reported. Requires deployed version and three paired repetitions per task. |
| DEV-18 | In progress | Local candidate and public-safety audit exist. Public URL, cloud revision and deployed acceptance are pending; no deck/video/submission copy was created. |

## Verified commands

- `python -m ruff check .`: passed.
- `python -m pytest -q`: 115 passed.
- `npm ci`, `npm run typecheck`, `npm run build`: passed; ten static routes exported.
- `npm audit --omit=dev --audit-level=high`: zero known vulnerabilities at the configured threshold.
- Local Docker build/start/health request: passed.

These results do not prove Firebase, Firestore, Cloud Run, Hosting or Vertex behavior in the target
Google Cloud project.
