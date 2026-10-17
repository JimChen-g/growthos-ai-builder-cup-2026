"""Run the offline Phase 4 Agent Action Ledger acceptance flow."""

import argparse
import asyncio

from backend.models.approval import ApprovalDecision, ApprovalOutcome
from backend.services.founder_experience import build_demo_founder_experience
from backend.services.phase2_runtime import build_demo_runtime


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision", choices=("pending", "approve", "reject"), default="pending")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    runtime = build_demo_runtime()
    experience = await build_demo_founder_experience(runtime).experience()
    ads_recommendation = next(
        item for item in experience.dashboard.recommendations if item.source_agent == "Ads Agent"
    )
    if args.decision != "pending" and ads_recommendation.workflow_run_id is not None:
        run = await runtime.orchestrator.get(ads_recommendation.workflow_run_id)
        await runtime.orchestrator.resume(
            run.run_id,
            ApprovalDecision(
                outcome=ApprovalOutcome(args.decision),
                actor="phase4-demo-founder",
                reason="Phase 4 deterministic audit-log demo decision",
                action_digest=run.approval.action_digest,
            ),
        )

    ledger = await runtime.audit_log.list()
    if args.json:
        print(ledger.model_dump_json(indent=2))
        return

    print("Agent Action Ledger")
    print(f"Recorded events: {ledger.total}")
    for entry in ledger.entries:
        print(
            " | ".join(
                [
                    entry.agent,
                    entry.action,
                    entry.reason,
                    entry.timestamp.isoformat(),
                    entry.status.value,
                ]
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
