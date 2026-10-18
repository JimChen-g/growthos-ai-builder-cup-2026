"""Offline scan scheduling: limit per-key bursts without retries or hidden quota."""

import asyncio
from time import monotonic

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


async def test_slow_first_serial_prompt_leaves_budget_for_later_answers():
    selected = request(ConnectorProvider.PERPLEXITY)
    asked = []
    cancelled = []
    closed = []

    class Provider:
        async def ask(self, prompt):
            asked.append(prompt)
            if prompt == selected.prompts[0]:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(prompt)
            await asyncio.sleep(0)
            return ProviderAnswer(answer=f"Acme: {prompt}", citations=[])

        async def aclose(self):
            closed.append(True)

    class Factory:
        async def build(self, tenant_id, provider):
            return Provider()

    quota = TenantQuotaLimiter(3)
    runner = GeoPromptRunner(
        Factory(), quota, request_timeout_seconds=1, total_timeout_seconds=0.3
    )
    started = monotonic()
    report = await runner.scan(TENANT, selected)
    assert monotonic() - started < 0.3
    assert asked == selected.prompts
    assert cancelled == [selected.prompts[0]] and closed == [True]
    assert report.providers[0].successful_prompts == 2
    assert "timed out" in report.results[0].error and not report.results[0].answer
    assert all(row.answer and not row.error for row in report.results[1:])
    assert await quota.used(TENANT, ConnectorProvider.PERPLEXITY) == 3
    assert runner._tenant_call_slots == {}


async def test_serial_prompt_budget_reuses_time_left_by_fast_answers():
    selected = request(ConnectorProvider.PERPLEXITY)

    class Provider:
        async def ask(self, prompt):
            # More than one third of the whole budget, but less than half the
            # remaining budget after the first answer completes immediately.
            if prompt == selected.prompts[1]:
                await asyncio.sleep(0.12)
            return ProviderAnswer(answer=f"Acme: {prompt}", citations=[])

        async def aclose(self):
            pass

    class Factory:
        async def build(self, tenant_id, provider):
            return Provider()

    report = await GeoPromptRunner(
        Factory(), TenantQuotaLimiter(3), total_timeout_seconds=0.3
    ).scan(TENANT, selected)
    assert report.providers[0].successful_prompts == 3


async def test_simultaneous_workspaces_share_global_cap_without_one_tenant_taking_all_slots():
    active = peak = 0
    tenant_active = {}
    tenant_peaks = {}
    release = asyncio.Event()

    class Provider:
        def __init__(self, tenant_id):
            self.tenant_id = tenant_id

        async def ask(self, prompt):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            tenant_active[self.tenant_id] = tenant_active.get(self.tenant_id, 0) + 1
            tenant_peaks[self.tenant_id] = max(
                tenant_peaks.get(self.tenant_id, 0), tenant_active[self.tenant_id]
            )
            if active == 6:
                release.set()
            try:
                await release.wait()
                return ProviderAnswer(answer=f"Acme: {prompt}", citations=[])
            finally:
                active -= 1
                tenant_active[self.tenant_id] -= 1

        async def aclose(self):
            pass

    class Factory:
        async def build(self, tenant_id, provider):
            return Provider(tenant_id)

    quota = TenantQuotaLimiter(30)
    runner = GeoPromptRunner(Factory(), quota, total_timeout_seconds=1)
    selected = GeoScanRequest(
        brand="Acme", website="https://example.test", category="support software",
        mode=DataMode.LIVE,
    )
    reports = await asyncio.gather(
        runner.scan("workspace-a", selected),
        runner.scan("workspace-a", selected),
        runner.scan("workspace-b", selected),
    )
    assert peak == 6 and active == 0
    assert tenant_peaks == {"workspace-a": 3, "workspace-b": 3}
    assert all(provider.successful_prompts == 3 for r in reports for provider in r.providers)
    for provider in selected.providers:
        assert await quota.used("workspace-a", provider) == 6
        assert await quota.used("workspace-b", provider) == 3
    assert runner._tenant_call_slots == {}


async def test_queued_scan_times_out_without_quota_while_checks_share_tenant_limit():
    occupied = asyncio.Event()
    asked = []
    closed = []

    class Provider:
        async def ask(self, prompt):
            asked.append(prompt)
            if len(asked) == 3:
                occupied.set()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.append(True)

    class Factory:
        async def build(self, tenant_id, provider):
            return Provider()

    quota = TenantQuotaLimiter(10)
    runner = GeoPromptRunner(
        Factory(), quota, request_timeout_seconds=1, total_timeout_seconds=0.03
    )
    checks = [
        asyncio.create_task(runner.connection_test(TENANT, ConnectorProvider.PERPLEXITY))
        for _ in range(3)
    ]
    try:
        await asyncio.wait_for(occupied.wait(), timeout=0.5)
        original_slots = runner._tenant_call_slots[TENANT][0]
        report = await runner.scan(TENANT, request(ConnectorProvider.PERPLEXITY))
        assert all(row.error and "timed out" in row.error for row in report.results)
        assert len(asked) == 3
        assert await quota.used(TENANT, ConnectorProvider.PERPLEXITY) == 3
        # A cancelled waiter must not remove the shared semaphore while checks
        # are still active or let another request create an independent limit.
        assert runner._tenant_call_slots[TENANT] == (original_slots, 3)
    finally:
        for task in checks:
            task.cancel()
        await asyncio.gather(*checks, return_exceptions=True)
    assert len(closed) == 4
    assert runner._tenant_call_slots == {}


async def test_cancelled_global_waiter_releases_tenant_state_without_leaking_capacity():
    runner = GeoPromptRunner(None, TenantQuotaLimiter(1))
    entered = asyncio.Event()

    async def hold(tenant_id):
        async with runner._bounded_call(tenant_id):
            entered.set()
            await asyncio.Event().wait()

    holders = []
    for tenant in ["a", "a", "a", "b", "b", "b"]:
        entered.clear()
        holders.append(asyncio.create_task(hold(tenant)))
        await asyncio.wait_for(entered.wait(), timeout=0.5)
    waiter = asyncio.create_task(hold("c"))
    await asyncio.sleep(0)
    assert runner._tenant_call_slots["c"][1] == 1
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert "c" not in runner._tenant_call_slots
    for task in holders:
        task.cancel()
    await asyncio.gather(*holders, return_exceptions=True)
    assert runner._tenant_call_slots == {}
    # All six global slots remain reusable after cancellation.
    for _ in range(6):
        await asyncio.wait_for(runner._call_slots.acquire(), timeout=0.5)
    for _ in range(6):
        runner._call_slots.release()
