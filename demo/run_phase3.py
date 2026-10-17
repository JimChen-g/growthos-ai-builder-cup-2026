"""Run the complete Phase 3 founder experience without credentials or network access."""

import asyncio

from backend.services.founder_experience import build_demo_founder_experience
from backend.services.phase2_runtime import build_demo_runtime


async def main() -> None:
    service = build_demo_founder_experience(build_demo_runtime())
    experience = await service.experience()
    briefing = experience.briefing
    dashboard = experience.dashboard
    feedback = dashboard.customer_intelligence

    print(briefing.salutation)
    print(f"Company: {briefing.company_name}")
    print(f"Growth Health: {briefing.growth_health}/100 ({briefing.health_band})")
    for signal in briefing.yesterday:
        print(
            f"{signal.label}: {signal.current_value} ({signal.change_pct:+.0f}% {signal.context})"
        )
    print(f"Main Risk: {briefing.main_risk}")
    print(f"Top Recommendation: {briefing.top_recommendation}")
    print("\nGrowth Dashboard")
    for metric in dashboard.metrics:
        print(f"- {metric.label}: {metric.formatted_value} · {metric.trend}")
    print("\nCustomer Intelligence")
    print(f"Sources: Reddit={feedback.source_counts['reddit']}, X={feedback.source_counts['x']}")
    print(f"Sentiment: {feedback.sentiment_score}/100 ({feedback.overall_sentiment})")
    print(f"Users complain: {feedback.main_problem}")
    print(f"Recommendation: {feedback.recommendation}")
    print(f"Pending approval: {briefing.pending_approval_id}")


if __name__ == "__main__":
    asyncio.run(main())
