"""Offline scan scheduling: limit per-key bursts without retries or hidden quota."""

import asyncio

import pytest

from backend.models.connectors import ConnectorProvider, DataMode, GeoScanRequest
from backend.services.connector_security import TenantQuotaLimiter
from backend.services.real_intelligence import GeoPromptRunner
from integrations.base import IntegrationAPIError
from integrations.geo_providers import ProviderAnswer

TENANT = "fixture-workspace"


def request(provider):
    return GeoScanRequest(
        brand="Acme", website="https://example.test", category="support software",
        mode=DataMode.LIVE, providers=[provider],
    )


async def test_perplexity_serializes_single_key_requests_and_returns_every_measured_answer():
    active = peak = 0
    asked = []
    closed = []

    class Provider:
        async def ask(self, prompt):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            asked.append(prompt)
            try:
                # This fake endpoint deliberately rejects overlapping calls, matching
                # the observed burst failure without making any external request.
                if active > 1:
                    raise IntegrationAPIError("perplexity", 429, "fixture burst limit")
                await asyncio.sleep(0)
                return ProviderAnswer(answer=f"Acme measured response: {prompt}", citations=[])
            finally:
                active -= 1

        async def aclose(self):
            closed.append(True)

    class Factory:
        async def build(self, tenant_id, provider):
            assert tenant_id == TENANT and provider is ConnectorProvider.PERPLEXITY
            return Provider()

    quota = TenantQuotaLimiter(3)
    runner = GeoPromptRunner(Factory(), quota, total_timeout_seconds=1)
    selected = request(ConnectorProvider.PERPLEXITY)
    report = await runner.scan(TENANT, selected)
    assert peak == 1 and active == 0
    assert asked == selected.prompts
    assert closed == [True]
    assert report.providers[0].status == "completed"
    assert report.providers[0].successful_prompts == len(selected.prompts) == 3
    assert [row.prompt for row in report.results] == selected.prompts
    assert all(not row.error and "Acme measured response" in row.answer for row in report.results)
    assert await quota.used(TENANT, ConnectorProvider.PERPLEXITY) == 3
    assert await quota.used("another-workspace", ConnectorProvider.PERPLEXITY) == 0


@pytest.mark.parametrize("provider", [
    ConnectorProvider.OPENAI, ConnectorProvider.GEMINI, ConnectorProvider.GEMINI_VERTEX,
])
async def test_other_geo_providers_keep_three_parallel_prompts(provider):
    active = peak = 0
    release = asyncio.Event()

    class Provider:
        async def ask(self, prompt):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 3:
                release.set()
            try:
                await release.wait()
                return ProviderAnswer(answer=f"Acme: {prompt}", citations=[])
            finally:
                active -= 1

        async def aclose(self):
            pass

    class Factory:
        async def build(self, tenant_id, selected):
            assert selected is provider
            return Provider()

    quota = TenantQuotaLimiter(3)
    report = await GeoPromptRunner(Factory(), quota, total_timeout_seconds=0.2).scan(
        TENANT, request(provider)
    )
    assert peak == 3 and active == 0
    assert report.providers[0].successful_prompts == 3
    assert await quota.used(TENANT, provider) == 3


async def test_perplexity_usage_limit_errors_remain_honest_without_automatic_retry():
    asked = []

    class Provider:
        async def ask(self, prompt):
            asked.append(prompt)
            raise IntegrationAPIError("perplexity", 429, "fixture provider usage limit")

        async def aclose(self):
            pass

    class Factory:
        async def build(self, tenant_id, provider):
            return Provider()

    quota = TenantQuotaLimiter(3)
    selected = request(ConnectorProvider.PERPLEXITY)
    report = await GeoPromptRunner(Factory(), quota).scan(TENANT, selected)
    assert asked == selected.prompts
    assert report.providers[0].status == "unavailable"
    assert report.providers[0].successful_prompts == 0
    assert all(
        row.error and "usage limit" in row.error and not row.answer for row in report.results
    )
    assert await quota.used(TENANT, ConnectorProvider.PERPLEXITY) == 3
