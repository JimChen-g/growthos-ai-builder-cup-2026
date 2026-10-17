# Phase 5 — Demo Polish

Phase 5 freezes one competition-safe case and one exact three-minute presentation path. The story
runs through the real Founder Daily Briefing, Growth Dashboard, Agent Actions, and Agent Action
Ledger surfaces without adding a demo-only product tab. The Phase 4 approval boundary and Phase
4.1 anchored ledger sample remain unchanged.

Devpost copy, screenshots, video production, submission forms, and other submission materials are
explicitly outside this phase.

## P0-050 Fixed Demo Case

The single fixture is `data/demo_phase5_polish.json`:

```json
{
  "title": "AI Customer Support Startup",
  "team_size": 5,
  "mrr": "30000",
  "currency": "USD",
  "growth_problems": ["CAC rising", "AI visibility low"]
}
```

Its Pydantic contract rejects extra fields and scope drift. It also freezes the ordered source,
action, future-concept, and timeline sets. The endpoint is read-only and does not initialize an
agent workflow, create an audit event, or call an external provider.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/v1/demo` | Return the immutable case and 180-second script |

## P0-051 Fixed Three-Minute Flow

| Time | Stage | Fixed content |
|---|---|---|
| 00:00–00:30 | Problem | No Growth Team; Team 5; MRR $30k; CAC rising; AI visibility low |
| 00:30–01:00 | Connect Data | Stripe, Google Ads, Meta Ads |
| 01:00–02:00 | AI Diagnosis | Growth Score 68/100 and two evidence-backed problems |
| 02:00–02:40 | Agent Execution | Create Campaign and GEO Plan, with approval/audit guardrails |
| 02:40–03:00 | Future | Growth Passport and Growth Capital, both marked as future concepts |

The sequence is contiguous and totals exactly 180 seconds. Any boundary change fails fixture
validation and the Phase 5 acceptance suite.

## Story-led recording path

There is no `3-Minute Demo` navigation item or standalone presentation page. Recording follows the
same product journey a founder uses:

- Founder Daily Briefing establishes Team 5, $30k MRR, no Growth Team, rising CAC, and low AI
  visibility;
- Growth Dashboard connects the sources and explains the Growth Score;
- Agent Actions turns evidence into an approval-protected campaign and GEO plan;
- Agent Action Ledger records execution and verification state;
- the closing narration connects verified growth intelligence to future capital access.

The recording script is a founder story rather than a feature list. It preserves the distinction
between autonomous preparation, founder approval, controlled execution, and future capital access.

## Offline acceptance

```bash
.venv/bin/python -m demo.run_phase5
.venv/bin/python -m demo.run_phase5 --json
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
cd frontend && npm run typecheck && npm run build
```

## Acceptance checklist

- [x] P0-050 uses one fixed AI Customer Support Startup
- [x] Team is exactly 5 people and MRR is exactly $30k
- [x] Growth problems are exactly CAC rising and AI visibility low
- [x] Connected-source story is exactly Stripe, Google Ads, and Meta Ads
- [x] Diagnosis shows Growth Score and evidence-backed problems
- [x] Agent Execution shows Create Campaign and GEO Plan
- [x] Future closes on Growth Passport and Growth Capital without claiming availability
- [x] Timeline is exactly 00:00–03:00 with all five fixed boundaries
- [x] Demo API is deterministic, read-only, and has no workflow side effect
- [x] Real product pages carry the full founder story without a demo-only tab
- [x] Phase 4 audit data and Phase 4.1 proof receipt are unchanged
- [x] Devpost submission materials are not included
