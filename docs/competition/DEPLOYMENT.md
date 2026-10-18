# Competition deployment runbook

The bounded public prototype was deployed with owner authorization on 2026-10-17. Continue to use
this runbook for updates; confirm the allowed spend before expanding traffic, quotas, regions or
enabled services.

## Intended topology

- Firebase Hosting serves `frontend/out` and rewrites `/api/**` to Cloud Run.
- Cloud Run runs the FastAPI container with a dedicated service account.
- Firebase Anonymous Auth supplies Sample identities; Google sign-in supplies verified Live identities.
- Firestore stores workflows, approval state, cache and quotas; clients have no direct database
  access (`firestore.rules` denies all client reads/writes).
- Cloud Run uses service-account ADC to call `gemini-3.7-flash` on Vertex AI.

The deployed Cloud Run and Firestore region is `us-central1`; Firebase Hosting successfully pins
its `/api/**` rewrite to the deployed Cloud Run revision.

## Owner checks before mutation

1. Confirm the active project ID/number, billing account, budget/credit status, permitted region,
   API quotas and Firebase project association.
2. Confirm or create a dedicated Cloud Run runtime service account.
3. Grant only the permissions required to invoke Vertex AI and read/write the competition
   Firestore database. Deployment identities may additionally need Cloud Run, Artifact Registry,
   build and service-account-user permissions; do not grant those to the runtime identity.
4. Enable only the agreed APIs (Cloud Run, Cloud Build/Artifact Registry as applicable, Vertex AI,
   Firestore and Firebase services). API enablement and deployment can incur charges and require
   owner approval.
5. Enable Firebase Anonymous Auth and fill the public Firebase web values at frontend build time.
6. Create Firestore in Native mode and configure TTL on the `expires_at` field for competition
   cache/workflow collections. Merely writing the field does not enable TTL deletion.

## Runtime configuration

Use environment variables documented in the root `.env.example`. Required cloud values include
the verified project ID, Firebase project ID, Firestore database, Vertex location/model and
`COMPETITION_MODE=true`. Do not inject local credential files or API keys into the image.

## Acceptance sequence

1. Deploy a private/minimally exposed Cloud Run revision and verify `/health`.
2. Make one structured Vertex request and confirm its output passes evidence validation.
3. Verify Firestore persistence by creating a pending workflow, starting a fresh backend instance,
   and completing its simulated approval.
4. Exercise concurrent duplicate approvals and confirm only one execution claim succeeds.
5. Deploy Hosting, then test direct navigation/refresh for every route and the `/api/**` rewrite.
6. In two clean browser sessions, verify distinct workspaces and denied cross-session access.
7. Exercise live, cache, quota and forced-model-failure paths; capture revision IDs, timestamps and
   public URLs in the status document.
8. Run the public-repository scan again immediately before push.

## Deployed acceptance on 2026-10-17

- Public judge path: <https://reflected-jet-478308-t5.web.app/builder-cup/>
- Cloud Run service: `growthos-builder-cup-api`, revision
  `growthos-builder-cup-api-00001-rjk`, serving 100% of traffic in `us-central1`.
- `/health` returned `{"status":"ok","phase":"5"}` from the public Cloud Run URL.
- Firebase Anonymous Auth issued a browser identity accepted by the backend.
- One end-to-end analysis returned source `live_vertex` and model `gemini-3.7-flash` through the
  Hosting rewrite.
- The same browser session approved the first alternative and received `executed`, verifying the
  Firestore-backed approval and simulated-execution path.
- Firestore rules deny direct client access. TTL is enabled for the `expires_at` field on the
  `competition_cache`, `workflows` and `competition_quota` collection groups.
- Runtime cost bounds are zero minimum instances, two maximum instances, 20 requests per anonymous
  session per day and 200 analysis requests globally per day.

Two-session isolation, deployed multi-instance races, forced model failure and persistence across a
new Cloud Run revision remain follow-up acceptance cases; they are not implied by the smoke test.

## Public UX correction on 2026-10-17

- Backend source `d34064f` deployed as `growthos-builder-cup-api-00002-znh`, serving 100% of
  Cloud Run traffic. Hosting was released with its API rewrite pinned to that revision.
- `/health` passed. Hosting `/api/v1/capabilities` returned `public_demo_only: true` and
  `live_available: false`. CPU 1, memory 512 MiB, concurrency 20, max instances 2 and the dedicated
  runtime identity were preserved.
- An existing browser saved in Live mode was refreshed and safely restored to the public sample
  workspace. The unavailable Live/Connections controls are absent; direct `/connections/` explains
  the public limitation instead of making a forbidden account request.
- The competition tab is now AI Decision Demo (AI 决策演示 / AI 決策示範). It is intentionally
  separate from connected-account data and only appears in Sample mode in private deployments.
- Real Vertex analyses in English, Simplified Chinese and Traditional Chinese passed browser
  acceptance. Evidence, priority, reasons, both alternatives, metrics and missing information
  followed the selected language. Switching language removed the previous-language result during
  analysis; a repeat Traditional Chinese analysis returned a correctly labelled localized cache hit.
- Simplified Chinese approval returned a localized simulated-execution record; Traditional Chinese
  rejection returned a localized no-execution confirmation. No external account actions occurred.
- Today, Growth, Actions, Customers and Visibility loaded through the public site. Public Actions
  uses the session-isolated competition workflow, while shared legacy workflows, approvals and
  ledgers are blocked server-side. Cross-user workflow read/approval denial is covered by API tests;
  deployed multi-session and multi-instance race tests are still not claimed here.
- Additional regression coverage includes expired Firebase token refresh, one bounded 401 retry,
  validation-error normalization and safe offline asset caching. Static export routes now use
  actual files and 404s instead of treating every missing asset as the home page.

## Project inventory on 2026-10-17

- The target project and linked billing account are accessible in the owner console.
- Cloud Run, Cloud Build, Artifact Registry, Firestore, Firebase, Firebase Hosting and Identity
  Toolkit services are enabled alongside Vertex AI.
- Firebase association, Native-mode Firestore, Anonymous Auth, the dedicated runtime service
  account and live model access passed the deployed smoke test described above.

## Self-service Live configuration

The owner authorized Google sign-in, encrypted private storage, seven-day retention and publication
on 2026-10-17. This restores the existing read adapters; it does not migrate operator credentials,
enable advertising writes or add a new Ads OAuth consent flow.

- Keep `PUBLIC_DEMO_ONLY=true` to protect shared legacy workflows and set
  `LIVE_SELF_SERVICE_ENABLED=true`, `LIVE_STORAGE_BACKEND=firestore`,
  `LIVE_DATA_RETENTION_DAYS=7`, `LIVE_REQUEST_TIMEOUT_SECONDS=45`.
- Supply `CONNECTION_VAULT_KEY` from Secret Manager `growthos-live-vault-key`, version `1`.
  The dedicated runtime identity has the secret-scoped accessor grant. Never rotate this key
  without a migration plan for still-valid encrypted credentials.
- Enable Google sign-in alongside Anonymous Auth and keep the Hosting domains authorized.
  The approved public-facing project name is GrowthOS Builder Cup.
- Enable Firestore TTL on `expires_at` for `connections`, `provider_quota`,
  `intelligence_history` and `audit_log`. Application reads reject expired documents immediately;
  physical TTL deletion is asynchronous. Quota documents expire after two days; credentials and
  private records after seven days. TTL may take time to become active after configuration.
- Verified Firebase UID determines private workspace ownership server-side. Requests cannot choose
  a workspace through tenant headers or body values. Browser access to Firestore remains denied.
- Judges use Google sign-in, switch to Live, then fill their own credentials under Connections.
  Stripe needs a restricted key. Google Ads needs a developer token, customer ID and an access
  token or complete OAuth refresh credentials. Meta needs its token/account configuration.
  Perplexity/Reddit and X use the existing individual provider configuration forms.
- Provider tests are independent. The original aggregate Growth report requires Stripe, Google
  Ads and Meta Ads together; its market/execution scoring defaults are disclosed assumptions.
  Missing accounts produce setup guidance, not a fallback to Sample. Visibility/Customers operate
  independently. Disconnect removes saved credentials in this application, not platform tokens.
- Release checks include hosted identity isolation, anonymous rejection, no operator-secret
  fallback, private cache headers, encrypted persistence, quota concurrency, timeouts and logging
  redaction. Offline tests do not establish new real-provider account acceptance.

Rebuild the frontend with public Firebase web identifiers. After publishing Cloud Run, publish
Hosting again because `pinTag` otherwise continues to use the previous API revision.

### Live release verification, 2026-10-17

- Backend source `9c89263` is deployed as `growthos-builder-cup-api-00003-stx`, serving 100%
  of Cloud Run traffic. Hosting has been published with its API rewrite pinned to this revision.
- Direct `/health` and Hosting `/api/v1/capabilities` passed. The latter reports
  `public_demo_only: true`, `live_available: false`, `live_self_service_enabled: true`:
  shared legacy access stays closed while verified self-service Live is available.
- Google and Anonymous sign-in show Enabled in Firebase. Secret Manager key version 1 exists;
  its only direct accessor binding is the dedicated runtime service account. All four new TTL
  rules show ACTIVE. Max instances 2, concurrency 20 and request timeout 55 seconds are preserved.
- Anonymous public UI can select Live and navigate Today, Growth and Connections; each presents
  the localized sign-in gate rather than a generic error or Sample data. Unauthenticated direct
  Connections requests return 401 with `Cache-Control: private, no-store`.
- Backend validation: 211 tests passed. Frontend validation: 21 tests, typecheck and production
  build passed. Frontend source `22a835f` was built and published successfully after the backend.
  Real provider credentials were not migrated or retested.
- The in-app browser did not display the Google popup during acceptance. The owner then explicitly
  confirmed successful Google sign-in and entry to Connections in their normal browser. This is
  user-reported login acceptance, not an agent-run two-account isolation or real-provider read test.
  The frontend bounds popup waiting and retains visible retry guidance instead of an endless spinner.

### Logged-in Live acceptance corrections, 2026-10-18

- Today and Connections were inspected in an authenticated empty workspace. Growth now checks
  saved configuration before enabling an explicit read, lists each missing business provider and
  links to Connections. Configuration alone is not described as a successful provider test.
- Connection mutations await the status refresh. A failed refresh has a separate retry that does
  not replay the credential save or disconnect. Credentials are not echoed in error messages.
- Private authentication failures invalidate the visible Live session after one bounded token
  refresh; reauthentication guidance replaces stale private content. Old-identity responses cannot
  clear a newly signed-in user's session.
- All three business connections save a reporting currency. Google/Meta verify it against account
  metadata before returning monetary metrics. Their currencies must match for spend-weighted
  scoring: no automatic FX conversion is performed. Stripe is independently labelled and filters
  to the selected supported currency, with correct zero-decimal charge/price units. MRR is labelled
  monthly recurring revenue, not total monthly revenue.
- Incomplete configuration and mixed Ads currencies are rejected before quota consumption. A
  successful report survives audit persistence failure and explicitly reports `audit_recorded:
  false`; its audit write has a three-second bound within the overall request deadline. History
  refresh failures remain separate from business-data read failures.
- Regression tests exercise the real provider parsers and aggregation with deterministic HTTP
  fixtures, plus tenant isolation, provider rejection, quotas and storage outages. These tests do
  not establish real-provider account acceptance. Judges must enter their own valid read-only
  credentials, test the sources, and load Growth; no operator credentials are migrated.

Release verification: source `4d6e5c3` was published as Cloud Run revision
`growthos-builder-cup-api-00004-8qz` with 100% traffic, and Hosting was published with its API
rewrite pinned to that revision. The runtime identity, CPU 1, memory 512 MiB, max instances 2,
concurrency 20 and timeout 55 seconds remain unchanged. Health and capabilities checks passed;
unauthenticated Connections returned 401 with `Cache-Control: private, no-store`.

All 251 backend tests and 38 frontend tests passed, along with Ruff, TypeScript and the production
build. In the authenticated public browser, Today, Growth and Connections all loaded the real empty
workspace (0/3 business providers), without the previous generic failure. Growth disabled the read
button and named the missing sources; Connections exposed the blank currency field and disabled
save/test until configured. Growth setup guidance was checked in English, Simplified Chinese and
Traditional Chinese. Actual provider data and nonempty deployed states remain pending the judge's
own credentials; they are covered only by controlled offline regression fixtures at this stage.

### Independent-review corrections, 2026-10-18

- Live reports use the last 30 calendar dates completed in UTC and both verified Ads account
  timezones. The common end date can be earlier than UTC yesterday; each source retains its own
  local day boundaries. The page exposes the interval, source timezone and collection timestamp.
  Attribution can still change after the reporting day closes.
- Google Ads totals include historical metrics for removed campaigns. Account currency and
  timezone are verified before reporting; a missing timezone fails explicitly, without guessing.
- Stripe MRR uses the documented `net_recurring_v1` policy: supported active/past-due licensed
  recurring items, normalized monthly, after supported permanent discounts. Customer discounts
  are inherited unless a subscription overrides them. Unsupported or incomplete pricing,
  discounts, permissions or bounded coupon lookups produce a null MRR with a reason, not a false
  zero or an undiscounted complete value. Period cash receipts remain separate from current MRR.
- Monetary display preserves meaningful fractional values. Sample pages share the same fixture
  period, MRR, Google-specific/combined ROAS and visibility values; invented daily deltas are removed.
- Connections distinguishes saved configuration from a live provider check, shows private
  workspace identity and credential expiry, and fails closed if credentials disappear between
  reads. Test/live/unknown environment is explicit; a real API call against a test account does
  not establish production-data accuracy.
- With separate explicit owner approval, five legacy connections were restored only to that
  owner's verified private workspace using encrypted, create-only storage with seven-day expiry.
  No existing record was overwritten and no local credentials or advertising state were changed.
  This does not change judge workspaces or automatically migrate other users' credentials.
- Today briefing enrichment and the non-blocking popup COOP warning remain deferred by request.

Release verification and real-provider outcomes are recorded after deployment; this section alone
does not claim a successful provider read or an independently reconciled production report.
