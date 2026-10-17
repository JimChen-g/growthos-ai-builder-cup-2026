"""Run the Phase 1 growth-intelligence demo or configured live providers."""

import argparse
import asyncio
from decimal import Decimal

from backend.config import Settings
from backend.services.growth_intelligence import GrowthIntelligenceReport, run_growth_intelligence


def display_decimal(value: Decimal) -> str:
    """Render exact decimal metrics without unnecessary trailing zeroes."""

    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def format_report(report: GrowthIntelligenceReport) -> str:
    """Render the compact acceptance output used by the Phase 1 demo."""

    components = report.growth_score.components
    return "\n".join(
        [
            "Company:",
            report.company.company_name,
            "",
            "MRR:",
            f"{display_decimal(report.revenue.mrr)} {report.revenue.currency}",
            "",
            "Google Ads ROAS:",
            display_decimal(report.google_ads.roas or Decimal("0")),
            "",
            "Meta CAC:",
            f"{display_decimal(report.meta_ads.cac or Decimal('0'))} {report.meta_ads.currency}",
            "",
            "Growth Score:",
            f"{display_decimal(report.growth_score.score)}/100",
            "",
            "Revenue Momentum:",
            display_decimal(components["revenue_momentum"]),
            "",
            "Marketing Efficiency:",
            display_decimal(components["marketing_efficiency"]),
            "",
            "Data Source:",
            report.data_source_mode.value,
        ]
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("demo", "live"),
        help="Override DATA_SOURCE_MODE from .env.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full normalized report as JSON.",
    )
    args = parser.parse_args()
    overrides = {"data_source_mode": args.mode} if args.mode else {}
    report = await run_growth_intelligence(Settings(**overrides))
    print(report.model_dump_json(indent=2) if args.json else format_report(report))


if __name__ == "__main__":
    asyncio.run(main())
