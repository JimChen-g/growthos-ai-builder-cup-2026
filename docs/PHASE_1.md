# Phase 1 — Growth Intelligence MVP

## Scope and boundary

Phase 1 adds the data contracts and deterministic intelligence needed to understand one company. All provider adapters are read-only. This phase does **not** add autonomous agent execution, ad mutations, payment mutations, approval workflows, or deep UI work.

## Delivered modules

| Area | Files | Result |
|---|---|---|
| Company context | `backend/models/company.py`, `data/company_profile.example.json` | Validated Company Profile with the six requested fields plus an explicit currency code |
| Runtime configuration | `backend/config.py`, `.env.example` | Optional environment-backed secrets; offline scoring starts without credentials |
| Shared integration contracts | `integrations/base.py`, `integrations/models.py`, `integrations/metrics.py` | Provider-safe errors, normalized snapshots, and reusable deterministic calculations |
| Stripe | `integrations/stripe.py` | Revenue, unique paying customers in period, active-subscription MRR, period growth, momentum score |
| Google Ads | `integrations/google_ads.py` | Campaign, spend, click, conversion, ROAS, CAC, comparison trend, efficiency score, health flags |
| Meta Ads | `integrations/meta_ads.py` | Campaign and ad/creative rollups, spend, click, purchase conversion/value, ROAS, CAC, trend, health flags |
| Growth score | `backend/services/growth_score.py` | Transparent V1 formula with component scores, weights, contributions, total, and band |
| Aggregation and source switching | `backend/services/growth_intelligence.py` | One unified report contract and the same orchestration path for synthetic demo data or configured live providers |
| Gemini boundary | `agents/gemini_growth_advisor.py` | Injected structured generator for narration only; cannot change scores or call tools |
| Acceptance demo | `data/demo_ai_customer_support.json`, `demo/run_phase1.py` | Runnable AI Customer Support Agent case with exact acceptance metrics and optional JSON output |
| Verification | `tests/` | Validation, scoring, Gemini-boundary, pagination-shape, and provider normalization tests |

## Company Profile contract

Required fields:

- `company_name`
- `industry`
- `website`
- `target_market`
- `stage`
- `monthly_revenue`

The model also records `currency`, defaulting to `USD`, so monetary values are not ambiguous. Unknown fields and negative monthly revenue are rejected.

## Metric semantics

### Stripe

- **Revenue:** successful captured charge amount minus refunded amount for the inclusive UTC date range and configured currency.
- **Customers:** unique Stripe customer IDs on eligible charges during the period. It is not the all-time customer count.
- **MRR:** active subscription item prices normalized to a monthly amount. Monthly, annual, weekly, and daily recurring intervals are supported.
- **Growth trend:** revenue change versus the immediately preceding date range of equal length.
- **Revenue Momentum Score:** neutral `50` when a comparison is unavailable; otherwise `50 + growth%`, clamped to `0..100`.

### Google Ads and Meta Ads

- **ROAS:** conversion value divided by spend.
- **CAC:** spend divided by conversions.
- **Trend:** ROAS change versus the immediately preceding date range of equal length.
- **Marketing Efficiency Score:** 70% ROAS target attainment plus 30% conversion-rate target attainment, clamped to `0..100`.
- **Health problems:** deterministic flags for no conversions, below-target ROAS, above-target CAC, and a ROAS decline of at least 10%.

Meta conversion types are configurable. The adapter takes the first configured matching purchase action for each ad row so aliases are not double-counted.

## Growth Score Engine V1

```text
Growth Score =
  Revenue Momentum      × 30%
  + Marketing Efficiency × 30%
  + Market Demand         × 20%
  + Execution Capability  × 20%
```

`Revenue Momentum` and `Marketing Efficiency` can be populated from provider snapshots. `Market Demand` and `Execution Capability` are explicit assessed inputs in Phase 1. The result includes every component and contribution for auditability.

For the reference inputs `70 / 60 / 80 / 65`, the output is `68.0 / 100`.

The runnable acceptance demo uses `80 / 65 / 60 / 62.5`, which also produces
`68.0 / 100` while matching the required Revenue Momentum and Marketing Efficiency
headline values.

## Demo and live data sources

`DATA_SOURCE_MODE=demo` loads a validated synthetic case without credentials or network
access. `DATA_SOURCE_MODE=live` loads the configured company profile, collects read-only
Stripe, Google Ads, and Meta Ads snapshots, and feeds the same normalized report and score
pipeline.

Run either mode from the repository root:

```bash
.venv/bin/python -m demo.run_phase1 --mode demo
.venv/bin/python -m demo.run_phase1 --mode live
```

The demo acceptance output is:

```text
Company: AI Customer Support Agent
MRR: 30000 USD
Google Ads ROAS: 2.8
Meta CAC: 120 USD
Growth Score: 68/100
Revenue Momentum: 80
Marketing Efficiency: 93
Data Source: demo
```

## Configuration items

| Variable | Required for | Notes |
|---|---|---|
| `DATA_SOURCE_MODE` | Runtime selection | `demo` by default; set to `live` to call configured read-only providers |
| `DEMO_FIXTURE_PATH` | Demo mode | Defaults to `data/demo_ai_customer_support.json` |
| `COMPANY_PROFILE_PATH` | Live mode | Company context used with live provider snapshots |
| `ANALYSIS_DAYS` | Live mode | Inclusive reporting period; defaults to 30 days |
| `MARKET_DEMAND_SCORE` | Live Growth Score | Explicit assessed Phase 1 input in the `0..100` range |
| `EXECUTION_CAPABILITY_SCORE` | Live Growth Score | Explicit assessed Phase 1 input in the `0..100` range |
| `STRIPE_SECRET_KEY` | Live Stripe read | Use a restricted/read-only key where possible |
| `STRIPE_CURRENCY` | Stripe | Defaults to `usd`; other currencies are excluded from the snapshot |
| `STRIPE_API_BASE_URL` | Stripe | Defaults to Stripe's API host |
| `GOOGLE_ADS_ACCESS_TOKEN` | Live Google Ads read | Phase 1 accepts a current OAuth access token; refresh orchestration is deferred |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Live Google Ads read | Sent in the provider-required request header |
| `GOOGLE_ADS_CUSTOMER_ID` | Live Google Ads read | Hyphens are normalized away |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | Manager-account read | Optional |
| `GOOGLE_ADS_API_VERSION` | Google Ads | Defaults to `v25`, verified for this Phase 1 build on 2026-08-09 |
| `GOOGLE_ADS_CURRENCY` | Google Ads | Defaults to `USD`; set it to the ad account's billing currency |
| `GOOGLE_ADS_TARGET_ROAS` | Google Ads score | Defaults to `3.0` |
| `GOOGLE_ADS_TARGET_CAC` | Google Ads diagnosis | Optional; enables the `High CAC` flag |
| `GOOGLE_ADS_TARGET_CONVERSION_RATE_PCT` | Google Ads score | Defaults to `3.0` |
| `META_ADS_ACCESS_TOKEN` | Live Meta Ads read | No token is committed |
| `META_ADS_AD_ACCOUNT_ID` | Live Meta Ads read | `act_` prefix is optional |
| `META_ADS_API_VERSION` | Live Meta Ads read | Intentionally explicit so it matches the version enabled for the Meta app |
| `META_ADS_CURRENCY` | Meta Ads | Defaults to `USD`; set it to the ad account's billing currency |
| `META_ADS_TARGET_ROAS` | Meta Ads score | Defaults to `3.0` |
| `META_ADS_TARGET_CAC` | Meta Ads diagnosis | Optional |
| `META_ADS_TARGET_CONVERSION_RATE_PCT` | Meta Ads score | Defaults to `3.0` |
| `GEMINI_API_KEY` | Future Gemini implementation | Reserved and unused by default in Phase 1 |
| `GEMINI_MODEL` | Future Gemini implementation | Reserved and unused by default in Phase 1 |

Copy `.env.example` to `.env` locally and populate only the providers being tested. `.env` and `.env.*` remain ignored; `.env.example` is the only allowed template.

## API boundaries

- Stripe uses list-only charge and subscription endpoints with cursor pagination.
- Google Ads uses `GoogleAdsService.SearchStream` with GAQL and no mutate operation.
- Meta Ads uses the Insights endpoint at ad level and no create/update/delete operation.
- Provider errors are converted into sanitized integration errors; response bodies and tokens are not included in exception text.

## Acceptance record

Automated acceptance runs against deterministic mocked provider responses, so it does not need secrets or network access. Read-only live smoke testing started on 2026-08-10 using credentials stored only in the ignored local `.env` file.

| Check | Result |
|---|---|
| Company Profile validation | Implemented; automated test added |
| Stripe normalization and MRR | Implemented; automated test added |
| Google Ads health output | Implemented; automated test added |
| Meta campaign/creative output | Implemented; automated test added |
| Growth Score `68.0 / 100` reference case | Implemented; automated test added |
| Gemini narration schema boundary | Implemented; automated test added |
| Unified AI Customer Support Agent demo | Passed — MRR `30000 USD`, Google ROAS `2.8`, Meta CAC `120 USD`, Growth Score `68/100`, Revenue Momentum `80`, Marketing Efficiency `65` |
| Demo/live source replacement | Passed — both modes use `GrowthIntelligenceService`; demo is offline and live constructs the three read-only adapters from environment configuration |
| Stripe live access | Passed — Test Mode authentication, Charges, and Subscriptions reads succeeded; the empty account correctly returned revenue `0.00`, customers `0`, and MRR `0.00` |
| Google Ads live access | Passed on 2026-08-11 — OAuth, test Manager hierarchy, child advertiser access, `SearchStream`, and two-period campaign reporting succeeded; the empty test account correctly returned zero campaigns, spend, clicks, and conversions |
| Meta Ads live access | Passed on 2026-08-11 — Graph API `v26.0` account read and two-period Insights requests succeeded with `ads_read`; account ID and USD currency matched local configuration, and the empty test account correctly returned zero campaigns, creatives, spend, clicks, and conversions |

Latest local verification on 2026-08-11:

| Command | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | Passed — 12 tests |
| `.venv/bin/python -m ruff check .` | Passed — no lint errors |
| `.venv/bin/python -m ruff format --check .` | Passed — 27 files formatted |
| `.venv/bin/python -m compileall -q agents backend integrations` | Passed |
| `.venv/bin/python -m pip check` | Passed — no broken requirements |
| `git diff --check` | Passed — no whitespace errors |

The provider tests use mocked API responses and exercise normalization without secrets or network access. Stripe, Google Ads, and Meta Ads have all passed read-only live acceptance. Production Google Ads accounts still require an appropriate Developer Token access level; the Phase 1 test integration uses a separate Google Ads test hierarchy.
