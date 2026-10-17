# Competition deployment runbook

The bounded public prototype was deployed with owner authorization on 2026-10-17. Continue to use
this runbook for updates; confirm the allowed spend before expanding traffic, quotas, regions or
enabled services.

## Intended topology

- Firebase Hosting serves `frontend/out` and rewrites `/api/**` to Cloud Run.
- Cloud Run runs the FastAPI container with a dedicated service account.
- Firebase Anonymous Auth issues short-lived client identity tokens.
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

## Project inventory on 2026-10-17

- The target project and linked billing account are accessible in the owner console.
- Cloud Run, Cloud Build, Artifact Registry, Firestore, Firebase, Firebase Hosting and Identity
  Toolkit services are enabled alongside Vertex AI.
- Firebase association, Native-mode Firestore, Anonymous Auth, the dedicated runtime service
  account and live model access passed the deployed smoke test described above.
