# Phase 4 — Trust Layer

Phase 4 implements only P1-040: a founder-facing Agent Action Ledger (Audit Log). It records the
agent identity, action, reason, timestamp, and execution status for the existing GrowthOS agent
workflow and displays those events inside the Founder Dashboard.

This phase does **not** implement blockchain, on-chain hashes, tokenization, RWA, wallets, or any
other distributed-ledger feature.

## P1-040 Agent Action Ledger

Each workspace has its own append-only, concurrency-safe runtime service. Existing entries are never edited;
an approval outcome or execution result is represented by a new immutable event. Idempotency keys
prevent a retried workflow node from duplicating the same logical event.

Demo and Live are separate ledger instances and separate read paths. A Live history request never
initializes the deterministic Demo workflow. Demo workflows and approval decisions write only to
the Demo ledger; explicit Live business refreshes, brand-visibility scans, and feedback collection
write only to the Live ledger.

The current workflow records:

- `GEO Agent` — AI-search visibility assessment, `executed`;
- `Ads Agent` — prepared Google Search campaign, `awaiting_approval`;
- `Feedback Agent` — Reddit/X customer-feedback analysis, `executed`;
- `Ads Agent` — campaign creation result after a decision, `executed`, `rejected`, or `failed`.

The approval gate remains unchanged. A pending or rejected action cannot call the Google Ads
writer. An executed ledger event carries the same `workflow_run_id` and `approval_id` as the
approved mutation.

## Data structure

```json
{
  "entry_id": "8cb6831d-4f60-47b8-bc50-b81ff7a62e7e",
  "mode": "demo",
  "agent": "Ads Agent",
  "action": "Created Google Search campaign",
  "reason": "Improve CAC — CAC increased: Creative fatigue",
  "timestamp": "2026-08-15T08:00:00Z",
  "status": "executed",
  "workflow_run_id": "workflow-uuid",
  "approval_id": "approval-uuid"
}
```

Allowed statuses are `awaiting_approval`, `executed`, `rejected`, and `failed`. The two linkage
fields are nullable because some agent analyses do not require an approval.

The collection response is:

```json
{
  "mode": "demo",
  "entries": [],
  "total": 0
}
```

## API change

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/v1/audit-log` | Newest-first Agent Action Ledger |

Optional query parameters:

- `status`: filter by one allowed execution status;
- `mode`: choose the isolated `demo` or `live` ledger, default `demo`;
- `limit`: return 1–100 events, default 50.

The Demo endpoint initializes the same deterministic founder workflow used by the Demo Dashboard
on its first request. The Live endpoint does not initialize that workflow and returns an empty
history until a connected action is explicitly requested.

## Founder Dashboard location

`/dashboard` shows **Agent Action Ledger** as the full-width Trust Layer section immediately below
the growth-plan and customer-intelligence panels. Its five visible columns are Agent, Action,
Reason, Timestamp, and Status. The table has contained horizontal scrolling below the desktop
breakpoint, so a narrow screen does not create page-level overflow.

## Runtime boundary

The Phase 4 services follow the existing Phase 2 approval/runtime boundary and are intentionally
in-memory for this local competition build. Demo and Live are independent service instances.
Events survive repeated requests in one API process
but do not claim durable multi-process storage. A production release can replace the service with
an append-only database adapter without changing the API or UI contract.

## Offline acceptance

```bash
.venv/bin/python -m demo.run_phase4 --decision pending
.venv/bin/python -m demo.run_phase4 --decision approve
.venv/bin/python -m demo.run_phase4 --decision reject
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
cd frontend && npm run typecheck && npm run build
```

The Phase 4 acceptance suite verifies append-only ordering, idempotent retry handling, status
filtering, all required fields, workflow and approval linkage, successful execution logging, and
rejection without a provider write. Browser acceptance verifies the full-width desktop ledger and
contained narrow-screen scrolling.

## Acceptance checklist

- [x] Agent identity, action, reason, timestamp, and execution status are recorded
- [x] Events are append-only and protected against duplicate workflow delivery
- [x] Pending, executed, rejected, and failed outcomes have explicit status values
- [x] Executed/rejected campaign events link to their workflow and approval
- [x] Founder Dashboard displays the five required fields
- [x] Demo and Live ledgers are isolated; no sample event can appear in Live history
- [x] Pending and rejected actions preserve the existing no-write guarantee
- [x] Backend tests, lint, frontend typecheck, production build, and browser QA pass
- [x] No blockchain, hash-on-chain, token, wallet, or RWA feature is implemented
