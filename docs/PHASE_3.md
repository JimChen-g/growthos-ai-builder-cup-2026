# Phase 3 — Founder Experience

Phase 3 turns the Phase 1 intelligence and Phase 2 agent workflow into the founder-facing daily
operating experience. It implements only P0-030, P0-031, and P1-032. Submission polish, video,
marketing assets, authentication, persistence, and new execution providers remain outside scope.

Every founder-facing value now carries structured provenance. A `DEMO` value is explicitly labeled
as synthetic and names its fixture or deterministic agent source; a `LIVE` value must name the
connected provider that produced it. This prevents a fixture from being mistaken for production
data while keeping the no-credential competition demo complete.

## P0-030 Founder Daily Briefing

The home page at `/` now opens with:

- `Good Morning, Founder`;
- Growth Health from Growth Score Engine V1;
- yesterday's revenue, Google Ads ROAS, and AI Visibility changes;
- the highest paid-acquisition risk with Phase 2 evidence;
- the highest-priority recommendation;
- a visible link to the approval-gated Agent Action workspace.

`FounderBriefingAgent` accepts an optional structured-generation boundary for Gemini narration.
The numeric metrics, risk, and recommended action remain supplied facts and cannot be changed by
the model. The credential-free acceptance path uses deterministic narration so it remains fully
reproducible. Growth Health, Revenue, Google Ads ROAS, AI Visibility, the risk, and the top
recommendation each display their own source badge rather than relying on one page-level label.

## P0-031 Growth Dashboard

The `/dashboard` page displays exactly five scorecard metrics:

| Metric | Source |
|---|---|
| Growth Score | Phase 1 Growth Score Engine V1 |
| Revenue | Stripe snapshot |
| Marketing ROI | Combined Google Ads and Meta Ads conversion value / spend |
| AI Visibility | Phase 2 GEO Agent |
| Customer Sentiment | Phase 3 Feedback Agent |

The recommendation queue consumes the same Phase 2 workflow output. The Ads Agent recommendation
carries its `workflow_run_id`, `approval_id`, and current approval state into the dashboard; the
founder can open that exact run in `/agent?runId=...` to review and approve or reject it.

All five scorecards, the customer-intelligence summary, and every recommendation expose their own
`provenance` object. The Agent Actions page separately identifies its fixture-backed workflow and
simulated executor so a Demo approval can never be presented as a Google Ads production write.

## P1-032 Feedback Agent

The Feedback Agent accepts a bounded set of Reddit/X signals, classifies each message, identifies
complaint topics, calculates a transparent sentiment score, and returns:

- source counts and overall sentiment;
- positive and negative percentages;
- the main customer problem and impact;
- representative evidence;
- one prioritized product recommendation.

The included demo fixture is synthetic and produces `Onboarding difficulty` as the main complaint
and `78/100` customer sentiment. Phase 3 does not scrape Reddit or X or publish social content.
The complete Demo path covers input, problem detection, sentiment, evidence, recommendation, and
the dashboard handoff without requiring a provider credential.

## API changes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/v1/founder/briefing` | Daily briefing only |
| `GET` | `/api/v1/dashboard` | Five metrics, recommendations, and customer intelligence |
| `GET` | `/api/v1/founder/experience` | Combined first-screen payload |
| `GET` | `/api/v1/feedback/demo` | Synthetic Reddit/X customer intelligence |
| `POST` | `/api/v1/feedback/analyze` | Analyze caller-supplied Reddit/X signals |

Existing Phase 2 workflow, approval, and handoff endpoints are unchanged.

Founder-experience responses add a machine-readable provenance contract:

```json
{
  "provenance": {
    "mode": "demo",
    "label": "Stripe fixture",
    "detail": "Synthetic competition-demo revenue; Stripe is not called."
  }
}
```

The same object is attached to briefing signals, dashboard metrics, recommendations, and customer
intelligence. A frontend source badge is therefore presentation of an API guarantee, not a
hard-coded visual hint.

## Page structure

```text
frontend/app/
├── page.tsx                       Founder Daily Briefing
├── dashboard/page.tsx             Five-metric Growth Dashboard
├── customer-intelligence/page.tsx Reddit/X Customer Intelligence
└── agent/page.tsx                 Existing Phase 2 approval workspace
```

Shared components live in `frontend/components/`; typed browser API contracts live in
`frontend/lib/api.ts`.

## Offline acceptance

```bash
.venv/bin/python -m demo.run_phase3
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
cd frontend && npm run typecheck && npm run build
```

The Phase 3 acceptance suite verifies the exact five metrics (`68`, `$30,000`, `2.4x`, `40`,
`78`), the `+12% / +20% / +8%` briefing deltas, Reddit/X analysis, the onboarding complaint,
pending approval propagation, provenance on every value, and zero provider writes before approval.
