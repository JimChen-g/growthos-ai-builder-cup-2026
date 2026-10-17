# Phase 2 — AI Agent Core

Phase 2 implements the bounded workflow approved in the product plan. It adds orchestration,
campaign analysis and drafting, a real Google Ads campaign mutation primitive, explicit human
approval, lightweight GEO analysis, an approval UI, and a read-only Phase 3 handoff contract.

The acceptance demo uses a dedicated slowing-growth fixture so the frozen Phase 1 `68/100` case
and live-provider behavior remain unchanged. Its evidence is internally consistent and explicit:

```text
Founder: My growth is slowing.
Meta CAC: 100 → 150 (+50%)
Meta ROAS trend: -15%
Creative evidence: present
Diagnosis: CAC increased → Creative fatigue
Action: Create a new campaign with refreshed creative
Expected ROAS: 3.2
```

## Implemented workflow

The initial LangGraph invocation follows this exact graph:

```text
START
  → Collect Data
  → Analyze
  → Call Agents
  → Generate Plan
  → Approval
  → Execute
  → Measure
  → END
```

The `Execute` node does not skip the approval requirement. On the first invocation it records
`blocked pending explicit approval`; the workflow status becomes `awaiting_approval` and the
provider writer receives zero calls. A later human decision re-enters at `Approval`, then proceeds
to `Execute` and `Measure`.

The seven initial node results and the three resume node results are retained in the workflow
trace. Phase 2 uses in-memory workflow and approval state. PostgreSQL durability is intentionally
deferred rather than implied.

## P0-020 Growth Orchestrator

- Implemented with LangGraph `StateGraph` and async nodes.
- Reuses Phase 1's normalized Stripe, Google Ads, Meta Ads, and Growth Score contracts.
- Runs Ads and GEO agent analysis, emits one bounded executable action, pauses on approval, and
  records a measurement baseline.
- Does not claim immediate performance lift. After execution it schedules a seven-day measurement
  window.

## P0-021 Ads Agent and Google Ads execution

The Ads Agent returns a structured diagnosis with `problem`, `reason`, `recommendation`, and
supporting metrics for Google Ads and Meta Ads. It creates a reviewable Google Search draft with:

- campaign name;
- target audience;
- keywords;
- headlines and descriptions;
- final URL;
- total budget and duration;
- expected ROAS/CAC/conversions contract.

Normalized ad snapshots now retain both ROAS trend and CAC trend. The Creative Fatigue rule requires
all three signals—rising CAC, ROAS decline of at least 10%, and observed creative data—so the demo
does not manufacture a cause from a single metric.

The Google Ads write adapter sends one atomic `googleAds:mutate` request containing a budget
operation and a campaign operation. New campaigns are created as `PAUSED`. The approved total
budget is converted to the daily budget shown in the mutation payload. A failed response is stored
as a failed execution; it is never described as success.

The provider method is intentionally low-level. Application code reaches it only through
`CampaignExecutionService`, which atomically claims an approved action before the provider call.

## P0-022 Approval workflow

Allowed states:

```text
pending → approved → executing → executed
        ↘ rejected
                     ↘ failed
```

Safety properties:

- Every external write in the Phase 2 flow requires an explicit human decision.
- The approval stores a canonical SHA-256 digest over the action kind, full campaign payload,
  budget, currency, and expected KPI.
- A stale or changed payload cannot reuse the old approval.
- The `approved → executing` transition is atomic, preventing duplicate provider calls.
- Re-reading an executed workflow returns its recorded result instead of repeating the mutation.
- Rejection produces no provider call.
- `update_google_budget` is reserved as a side-effecting action kind and is subject to the same
  approval contract; no unapproved budget-update route exists.

The Founder UI accepts a founder message and displays the complete narrative before Approve/Reject:

```text
Founder statement
→ AI acknowledgement
→ Top Problems and evidence
→ Recommended Actions
→ Expected Impact
→ Campaign Draft
→ Pending / Approve / Execute
→ GEO problem and Optimization Strategy
```

It calls the combined decision-and-resume endpoint so status stays consistent.

## P0-023 GEO Agent

Phase 2 deliberately avoids a broad SEO crawler. It accepts auditable AI-answer observations and
returns:

- brand mention coverage and AI visibility score;
- `Missing`, `Underrepresented`, or `Visible` status;
- competitor mention count and prompt share;
- evidence-backed reasons;
- a three-item GEO Action Plan.

The synthetic demo tracks prompts such as “Best AI customer support tools” and shows the demo brand
as missing behind Intercom, Ada, Zendesk, and Forethought.

## API

Run the demo API:

```bash
.venv/bin/uvicorn backend.api.app:app --reload --port 8000
```

Key routes:

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/v1/workflows/demo` | Start with an optional `founder_message` JSON field |
| `GET` | `/api/v1/workflows/{run_id}` | Read the current workflow state and trace |
| `GET` | `/api/v1/approvals?status=pending` | Read the founder approval queue |
| `POST` | `/api/v1/workflows/{run_id}/decision` | Approve/reject and resume the workflow |
| `GET` | `/api/v1/workflows/{run_id}/phase3-handoff` | Read the Phase 3 contract |

Run the UI in a second terminal:

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`, enter the founder concern, choose **Analyze my business**, and review
the full diagnosis, action, approval, execution, and GEO story.

## Offline acceptance demo

Pending approval, with no provider write:

```bash
.venv/bin/python -m demo.run_phase2 --decision pending
```

Simulated human approval and offline execution result:

```bash
.venv/bin/python -m demo.run_phase2 --decision approve
```

Override the founder input when needed:

```bash
.venv/bin/python -m demo.run_phase2 \
  --founder-message "My growth is slowing." \
  --decision pending
```

Use `--decision reject` to verify that rejection produces no external action.

## Phase 3 boundary

Phase 2 exposes `Phase3Handoff` only. It contains Growth Score, revenue, marketing ROI, AI
visibility, optional customer sentiment, top recommendation, and any pending approval ID. Founder
Daily Briefing generation, the full Growth Dashboard, Reddit/X ingestion, and Feedback Agent are
not implemented in this phase.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
cd frontend && npm run typecheck && npm run build
```

The acceptance suite asserts the exact narrative (`CAC increased`, `Creative fatigue`, campaign
creation, expected ROAS 3.2), pending/approve/execute state transitions, reject-with-zero-write,
budget-digest invalidation, idempotent execution, GEO strategy output, and Phase 1 regression.
