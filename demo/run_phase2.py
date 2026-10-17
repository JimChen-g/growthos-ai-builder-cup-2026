"""Run the full offline Phase 2 workflow, including a simulated human decision."""

import argparse
import asyncio

from backend.models.approval import ApprovalDecision, ApprovalOutcome
from backend.services.phase2_runtime import DEMO_FOUNDER_MESSAGE, build_demo_runtime


def format_run(run) -> str:
    competitors = ", ".join(item.brand for item in run.geo_analysis.competitors)
    trace = " → ".join(entry.node for entry in run.trace if entry.attempt == 1)
    execution = (
        run.execution.campaign_resource_name if run.execution else "blocked before provider write"
    )
    primary = run.narrative.top_problems[0]
    geo_actions = " | ".join(item.action for item in run.geo_analysis.action_plan)
    return "\n".join(
        [
            f"Workflow: {trace}",
            f"Status: {run.status}",
            "Founder:",
            f'"{run.narrative.founder_message}"',
            "AI Growth Agent:",
            f'"{run.narrative.acknowledgement}"',
            f"Growth Score: {run.report.growth_score.score}/100",
            f"Problem: {primary.problem}",
            f"Reason: {primary.reason}",
            f"Evidence: {' | '.join(primary.evidence)}",
            f"Recommended Action: {run.narrative.recommended_actions[0]}",
            f"Action: {run.plan.proposed_action.summary}",
            f"Budget: {run.plan.proposed_action.budget} {run.plan.proposed_action.currency}",
            f"Expected Impact: ROAS {run.narrative.expected_impact.expected_roas}",
            run.narrative.approval_prompt,
            f"Approval: {run.approval.status}",
            (
                f"AI Visibility: {run.geo_analysis.visibility_score}/100 "
                f"({run.geo_analysis.brand_status})"
            ),
            f"Competitors: {competitors}",
            f"GEO Optimization Strategy: {geo_actions}",
            f"Execution: {execution}",
            f"Measurement: {run.measurement.status}",
        ]
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision", choices=("pending", "approve", "reject"), default="pending")
    parser.add_argument("--founder-message", default=DEMO_FOUNDER_MESSAGE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    runtime = build_demo_runtime()
    request = runtime.demo_request.model_copy(update={"founder_message": args.founder_message})
    run = await runtime.orchestrator.start(request)
    if args.decision != "pending":
        run = await runtime.orchestrator.resume(
            run.run_id,
            ApprovalDecision(
                outcome=ApprovalOutcome(args.decision),
                actor="demo-founder@example.com",
                reason="Phase 2 deterministic demo decision",
                action_digest=run.approval.action_digest,
            ),
        )
    print(run.model_dump_json(indent=2) if args.json else format_run(run))


if __name__ == "__main__":
    asyncio.run(main())
