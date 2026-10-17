# Phase 3.1 — Real Intelligence Connectors

Phase 3.1 adds real, read-only intelligence sources without entering Phase 4 Demo Polish. It keeps
the existing Phase 1–3 deterministic acceptance path and makes Demo versus Live provenance visible
on every new scan.

For competition convergence, Demo availability and Live connection state are modeled separately.
Every connector can participate in the complete synthetic walkthrough, but only a tested provider
connection may produce a result labeled `LIVE`. A failed Live request remains a visible error and
never borrows Demo data.

## Grounded GEO providers

The unified Prompt Runner sends the same neutral prompt set to each selected provider:

| Provider | API path | Grounding and evidence |
|---|---|---|
| OpenAI | Responses API | `web_search` is required; `url_citation` annotations are retained |
| Gemini · AI Studio | Gemini Developer API | `google_search` tool; model-output URL annotations are retained |
| Gemini · Vertex AI | Vertex AI `generateContent` | `googleSearch`; `groundingMetadata` web sources are retained |
| Perplexity | Sonar API | top-level `citations` and `search_results` are retained |

The runner detects whether the brand appears, estimates its rank among supplied competitors, and
checks whether a citation resolves to the brand's owned domain. Provider visibility is transparent:

```text
Visibility Score = Mention Coverage 60% + Mention Rank 25% + Owned Citation Coverage 15%
```

The overall score is the mean of providers that returned at least one successful prompt. A missing
credential or provider error produces `unavailable`; it never falls back to a Demo answer in Live
mode. API-measured visibility is a repeatable product metric, but it is not guaranteed to be
identical to the consumer ChatGPT, Gemini, or Perplexity applications.

## Customer intelligence

- X uses the official Recent Search endpoint and an app bearer token.
- Reddit does not scrape pages and does not claim broad Data API access. The fallback uses the
  Perplexity Search API with `search_domain_filter: ["reddit.com"]` to discover public Reddit URLs.
- Every collection reports its method, status, signal count, and error independently.

## Connection Center

`/connections` provides:

- OpenAI, Gemini AI Studio, Gemini Vertex AI, Perplexity, X, Reddit, Google Ads, Meta Ads,
  and Stripe status;
- encrypted API-key entry for OpenAI, Gemini, Perplexity, and X;
- a separate Vertex AI enterprise connection using Google Cloud Project ID, endpoint location,
  and either a Vertex AI API key or server Application Default Credentials (ADC);
- live connection tests;
- Google Ads and Meta Ads account selection for the accounts exposed by the configured server
  OAuth credentials;
- an Intercom-ready Demo/Live GEO scanner with clickable citations.

The page is deliberately simple by default:

1. use the complete Demo walkthrough without credentials;
2. open only the provider the customer needs;
3. follow at most three setup steps, save, and run a bounded connection test;
4. use the global workspace switch to enter Live mode only after the source reports ready.

Advanced credentials, project settings, account selection, and test controls remain collapsed
until the customer chooses **Set up connection** or **Manage connection**. Every connector card
shows one plain-language readiness state, the expected setup time, and its shortest supported
connection method:

| Connector | Simplest production path |
|---|---|
| Reddit | Automatic compliant reddit.com discovery after Perplexity is connected |
| X | One bearer token |
| ChatGPT / OpenAI | One server-side API key |
| Gemini AI Studio | One API key |
| Gemini Vertex AI | Google Cloud project + ADC or API key |
| Perplexity | One API key |
| Google Ads | Google OAuth, then choose an ad account |
| Meta Ads | Meta OAuth, then choose an ad account |
| Stripe | One restricted read-only key |

This increment does not implement the provider OAuth consent redirect, refresh-token exchange, or
multi-account discovery UI. Those require registered production apps and callback URLs. It exposes
the account selector and preserves the existing approval gate once server OAuth credentials are
configured.

## Credential and tenant security

- Browser code never receives a provider credential.
- Self-service keys are encrypted at rest with Fernet and written under the ignored
  `data/private/` directory.
- The Fernet master key is loaded from ignored server configuration (`CONNECTION_VAULT_KEY`).
- Vault records and quotas are namespaced by validated tenant ID.
- Environment credentials are available only to the special local tenant `local-demo`.
- Provider calls have a daily per-tenant/per-provider V1 quota.

The current tenant ID header is a local product boundary, not production authentication. A hosted
release must bind tenant ID to an authenticated server session, move the master key to a managed
KMS/secret manager, and move quotas/account selection to shared persistent storage.

## API changes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/v1/connections` | Safe connection and selected-account metadata |
| `PUT` | `/api/v1/connections/{provider}/credential` | Encrypt and save a tenant key |
| `PUT` | `/api/v1/connections/gemini_vertex/config` | Encrypt Vertex project, location, and optional API key |
| `POST` | `/api/v1/connections/{provider}/account` | Select an available Google/Meta ad account |
| `POST` | `/api/v1/connections/{provider}/test` | Run a bounded live test or safe configuration check |
| `POST` | `/api/v1/geo/scans` | Run the unified Demo or Live GEO scan |
| `POST` | `/api/v1/feedback/collect` | Collect labeled Demo or Live Reddit/X signals |

All routes accept `X-Tenant-ID`; local UI uses `local-demo`.

Each item returned by `GET /api/v1/connections` now includes:

- `demo_available` — whether the synthetic walkthrough is ready;
- `status` — the independent Live credential/account state;
- `setup_kind` and `setup_label` — the shortest connection method;
- `setup_steps` — one to three customer-facing steps;
- `estimated_setup_minutes` — a realistic first-run expectation.

### Gemini connection choices

- **Quick connection — AI Studio:** one Gemini Developer API key. This remains the lowest-friction
  path for supported accounts and regions.
- **Enterprise connection — Vertex AI:** Google Cloud Project ID plus `global`, `us`, `eu`, or a
  supported regional endpoint. An API key can be used for testing; ADC is the production default.

The two Gemini paths are separate GEO providers, so the same prompt can measure both without one
result overwriting the other. `global` is the default for availability. Customers with processing
location requirements must select a supported regional or multi-region endpoint instead.

## Real-brand acceptance

The frozen test brand is Intercom:

```text
Brand: Intercom
Website: https://www.intercom.com
Category: AI customer support
Competitors: Zendesk, Ada, Forethought
```

Run the non-billable source-boundary check:

```bash
.venv/bin/python -m demo.run_phase31 --mode demo
.venv/bin/python -m demo.run_phase31 --mode live
```

The second command makes real provider requests only for configured AI providers. If none are
configured, all providers remain explicitly unavailable and the overall score is `0.0`, proving
that Live never reuses synthetic data.

The verified competition setup uses Gemini on Vertex AI with the team's Google Cloud project,
the `global` endpoint, Google Search grounding, and server ADC. The
connection test and an Intercom Live scan both completed successfully. Credentials remain in the
encrypted local vault/server environment and are not documented or returned to the browser.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
cd frontend && npm run typecheck && npm run build
```
