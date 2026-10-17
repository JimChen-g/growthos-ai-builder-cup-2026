"""Run the Phase 3.1 Intercom GEO acceptance scan without exposing credentials."""

import argparse
import asyncio

from backend.models.connectors import ConnectorProvider, DataMode, GeoScanRequest
from backend.services.real_intelligence import build_real_intelligence_runtime


async def run(mode: DataMode) -> None:
    runtime = build_real_intelligence_runtime()
    center = await runtime.connections.center("local-demo")
    configured = [item.provider.value for item in center.connections if item.configured]
    # One logical Gemini source per scan: Vertex AI when configured, otherwise AI Studio.
    gemini = (
        ConnectorProvider.GEMINI_VERTEX
        if ConnectorProvider.GEMINI_VERTEX.value in configured
        else ConnectorProvider.GEMINI
    )
    report = await runtime.geo.scan(
        "local-demo",
        GeoScanRequest(
            brand="Intercom",
            website="https://www.intercom.com",
            category="AI customer support",
            competitors=["Zendesk", "Ada", "Forethought"],
            providers=[ConnectorProvider.OPENAI, gemini, ConnectorProvider.PERPLEXITY],
            mode=mode,
        ),
    )
    print(f"Brand: {report.brand}")
    print(f"Mode: {report.mode.value.upper()}")
    print(f"Configured connectors: {', '.join(configured) or 'none'}")
    print(f"Overall visibility: {report.overall_visibility_score}/100")
    for provider in report.providers:
        print(
            f"{provider.provider.value}: {provider.visibility_score}/100 · "
            f"{provider.successful_prompts}/{provider.attempted_prompts} successful · "
            f"{provider.brand_mentions} mentions · {provider.branded_citations} owned citations"
        )
    errors = sorted({item.error for item in report.results if item.error})
    for error in errors:
        print(f"Unavailable: {error}")
    print(f"Provenance: {report.provenance}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    args = parser.parse_args()
    asyncio.run(run(DataMode(args.mode)))


if __name__ == "__main__":
    main()
