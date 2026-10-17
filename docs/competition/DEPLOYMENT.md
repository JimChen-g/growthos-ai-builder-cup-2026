# Competition deployment runbook

This runbook is intentionally not marked complete. Owner-console access, project identity and a
linked billing account were verified read-only on 2026-10-17. Run mutations only after confirming
the allowed spend/credits and authenticating the same authorized identity in the deployment CLI.

## Intended topology

- Firebase Hosting serves `frontend/out` and rewrites `/api/**` to Cloud Run.
- Cloud Run runs the FastAPI container with a dedicated service account.
- Firebase Anonymous Auth issues short-lived client identity tokens.
- Firestore stores workflows, approval state, cache and quotas; clients have no direct database
  access (`firestore.rules` denies all client reads/writes).
- Cloud Run uses service-account ADC to call `gemini-3.7-flash` on Vertex AI.

The configured region `us-central1` is a candidate only until project availability and Hosting
rewrite compatibility are checked. Do not treat it as deployed evidence.

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

No cloud resource, public URL or live Vertex request has been asserted by this document.

## Read-only inventory on 2026-10-17

- The target project and linked billing account are accessible in the owner console.
- Agent Platform API (Vertex AI) and Cloud Datastore API are enabled.
- Cloud Run was not present in the enabled-service list inspected for this project.
- Firebase association, Firestore database creation, Anonymous Auth, Cloud Run service identity,
  deployment region, quotas and live model access are not yet accepted.
