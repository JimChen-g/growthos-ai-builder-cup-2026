"""Offline reporting-day and removed-campaign regressions; no real account requests."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

import backend.services.growth_intelligence as growth
from backend.config import Settings
from integrations.google_ads import GoogleAdsIntegration, GoogleAdsIntegrationConfig
from integrations.meta_ads import MetaAdsIntegration, MetaAdsIntegrationConfig
from integrations.models import AccountTimezoneError, AdsSnapshot, DateRange, RevenueSnapshot


@pytest.mark.parametrize(
    ("instant", "zones", "expected_end"),
    [
        ("2026-02-01T12:00:00+00:00", ("UTC", "UTC"), "2026-01-31"),
        # UTC has crossed midnight, but the US account has not: UTC-yesterday is open there.
        ("2026-02-01T00:30:00+00:00", ("America/Los_Angeles", "Asia/Shanghai"), "2026-01-30"),
        # Eastern accounts have entered tomorrow, but the Stripe UTC day is still open.
        ("2026-02-01T23:30:00+00:00", ("Asia/Tokyo", "Pacific/Auckland"), "2026-01-31"),
        ("2026-02-01T12:30:00+00:00", ("Pacific/Kiritimati", "Etc/GMT+12"), "2026-01-31"),
        # The date boundary follows DST rules, not a fixed offset.
        ("2026-03-09T06:30:00+00:00", ("America/Los_Angeles", "UTC"), "2026-03-07"),
        ("2026-03-09T07:30:00+00:00", ("America/Los_Angeles", "UTC"), "2026-03-08"),
        ("2026-11-02T07:30:00+00:00", ("America/Los_Angeles", "UTC"), "2026-10-31"),
        ("2026-11-02T08:30:00+00:00", ("America/Los_Angeles", "UTC"), "2026-11-01"),
        ("2028-03-01T12:00:00+00:00", ("UTC", "Asia/Shanghai"), "2028-02-29"),
    ],
)
def test_common_period_is_closed_in_all_source_timezones(instant, zones, expected_end):
    now = datetime.fromisoformat(instant)
    period = growth.complete_reporting_period(now=now, days=30, reporting_timezones=zones)
    assert period.end == date.fromisoformat(expected_end)
    assert (period.end - period.start).days + 1 == 30
    previous = period.previous_period()
    assert (previous.end - previous.start).days + 1 == 30
    assert (period.start - previous.end).days == 1
    assert all(period.end < now.astimezone(ZoneInfo(zone)).date() for zone in ("UTC", *zones))


def test_reporting_clock_requires_timezone_and_rejects_unknown_account_timezone():
    with pytest.raises(ValueError, match="include a timezone"):
        growth.complete_reporting_period(
            now=datetime(2026, 2, 1), days=30, reporting_timezones=("UTC",)
        )
    with pytest.raises(AccountTimezoneError):
        growth.complete_reporting_period(
            now=datetime(2026, 2, 1, tzinfo=UTC), days=30, reporting_timezones=("Unknown/Zone",)
        )


def google_config():
    return GoogleAdsIntegrationConfig(
        access_token="fixture_access", developer_token="fixture_developer", customer_id="123"
    )


def meta_config():
    return MetaAdsIntegrationConfig(
        access_token="fixture_meta", ad_account_id="123", api_version="v26.0"
    )


@pytest.mark.asyncio
async def test_google_removed_campaign_history_is_included_with_one_metadata_read():
    calls = []

    def handler(request):
        query = json.loads(request.content)["query"]
        calls.append(query)
        if "FROM customer" in query:
            assert "customer.currency_code" in query and "customer.time_zone" in query
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"customer": {"currencyCode": "USD", "timeZone": "America/Los_Angeles"}}
                    ]
                },
            )
        assert "campaign.status !=" not in query
        assert "campaign.status IN" not in query
        current = "2026-01-01" in query
        rows = []
        for campaign_id, status, spend, conversions, value in (
            ("active", "ENABLED", 100, 5, 250 if current else 300),
            (
                "historic",
                "REMOVED",
                50 if current else 20,
                5 if current else 1,
                50 if current else 60,
            ),
        ):
            rows.append(
                {
                    "campaign": {"id": campaign_id, "name": campaign_id, "status": status},
                    "metrics": {
                        "costMicros": str(spend * 1_000_000),
                        "clicks": "100",
                        "conversions": str(conversions),
                        "conversionsValue": str(value),
                    },
                }
            )
        return httpx.Response(200, json=[{"results": rows}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = GoogleAdsIntegration(google_config(), client=client)
        assert await adapter.verify_reporting_metadata() == "America/Los_Angeles"
        snapshot = await adapter.collect(DateRange(start="2026-01-01", end="2026-01-31"))

    assert len(calls) == 3  # One shared currency/timezone lookup, current period, previous period.
    assert snapshot.spend == Decimal("150")
    assert snapshot.conversions == Decimal("10")
    assert snapshot.conversion_value == Decimal("300")
    assert snapshot.roas == Decimal("2.0")
    assert snapshot.cac == Decimal("15.0")
    assert snapshot.trend_pct == Decimal("-33.3")
    assert snapshot.cac_trend_pct == Decimal("-25.0")
    assert snapshot.campaigns[1].status == "REMOVED"  # History does not imply an active target.
    assert snapshot.reporting_timezone == "America/Los_Angeles"
    assert snapshot.collected_at.tzinfo is not None


@pytest.mark.asyncio
async def test_meta_period_metadata_is_verified_once_and_returned_on_snapshot():
    requests = []

    def handler(request):
        requests.append(request)
        if not request.url.path.endswith("/insights"):
            assert request.url.params["fields"] == "currency,timezone_name"
            return httpx.Response(200, json={"currency": "USD", "timezone_name": "Asia/Shanghai"})
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = MetaAdsIntegration(meta_config(), client=client)
        assert await adapter.verify_reporting_metadata() == "Asia/Shanghai"
        snapshot = await adapter.collect(DateRange(start="2026-01-01", end="2026-01-30"))
    assert len(requests) == 3
    assert snapshot.reporting_timezone == "Asia/Shanghai"
    assert snapshot.period == DateRange(start="2026-01-01", end="2026-01-30")
    assert snapshot.collected_at.tzinfo is not None
    assert {json.loads(request.url.params["time_range"])["until"] for request in requests[1:]} == {
        "2026-01-30",
        "2025-12-31",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "meta"])
@pytest.mark.parametrize("zone", [None, "", "Unknown/private-account-value", "../private"])
async def test_missing_or_invalid_account_timezone_never_silently_falls_back(provider, zone):
    calls = []

    def handler(request):
        calls.append(request)
        if provider == "google":
            assert "FROM customer" in json.loads(request.content)["query"]
            return httpx.Response(
                200, json={"results": [{"customer": {"currencyCode": "USD", "timeZone": zone}}]}
            )
        assert not request.url.path.endswith("/insights")
        return httpx.Response(200, json={"currency": "USD", "timezone_name": zone})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = (
            GoogleAdsIntegration(google_config(), client=client)
            if provider == "google"
            else MetaAdsIntegration(meta_config(), client=client)
        )
        with pytest.raises(AccountTimezoneError) as error:
            await adapter.collect(DateRange(start="2026-01-01", end="2026-01-30"))
    assert len(calls) == 1
    assert "reporting timezone" in str(error.value)
    assert "private" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata_unavailable", [False, True])
async def test_live_orchestration_resolves_timezones_before_collecting_all_providers(
    monkeypatch, metadata_unavailable
):
    observed = []

    class Source:
        def __init__(self, provider, zone):
            self.provider, self.zone = provider, zone

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def verify_reporting_metadata(self):
            observed.append((self.provider, "metadata"))
            if metadata_unavailable and self.provider == "google_ads":
                raise AccountTimezoneError()
            return self.zone

        async def collect(self, period):
            observed.append((self.provider, period))
            if self.provider == "stripe":
                return RevenueSnapshot(
                    period=period,
                    currency="USD",
                    revenue=100,
                    customers=2,
                    mrr=50,
                    momentum_score=50,
                )
            return AdsSnapshot(
                source=self.provider,
                period=period,
                reporting_timezone=self.zone,
                currency="USD",
                campaigns=[],
                spend=0,
                clicks=0,
                conversions=0,
                conversion_value=0,
                efficiency_score=0,
            )

    monkeypatch.setattr(growth, "StripeIntegration", lambda _: Source("stripe", "UTC"))
    monkeypatch.setattr(
        growth, "GoogleAdsIntegration", lambda _: Source("google_ads", "America/Los_Angeles")
    )
    monkeypatch.setattr(growth, "MetaAdsIntegration", lambda _: Source("meta_ads", "Asia/Shanghai"))
    settings = Settings(
        _env_file=None,
        data_source_mode="live",
        analysis_days=30,
        stripe_secret_key="fixture_stripe",
        google_ads_access_token="fixture_google",
        google_ads_developer_token="fixture_dev",
        google_ads_customer_id="123",
        meta_ads_access_token="fixture_meta",
        meta_ads_ad_account_id="123",
        meta_ads_api_version="v26.0",
    )
    if metadata_unavailable:
        with pytest.raises(growth.GrowthSourceError, match="Google Ads:.*reporting timezone"):
            await growth.run_growth_intelligence(
                settings, now=datetime(2026, 2, 1, 0, 30, tzinfo=UTC)
            )
        assert observed == [("google_ads", "metadata"), ("meta_ads", "metadata")]
        return
    report = await growth.run_growth_intelligence(
        settings, now=datetime(2026, 2, 1, 0, 30, tzinfo=UTC)
    )
    assert observed[:2] == [("google_ads", "metadata"), ("meta_ads", "metadata")]
    assert all(
        period == DateRange(start="2026-01-01", end="2026-01-30") for _, period in observed[2:]
    )
    assert report.period_policy == "common_complete_calendar_days"
    assert report.revenue.period == report.google_ads.period == report.meta_ads.period
    assert report.google_ads.reporting_timezone == "America/Los_Angeles"
    assert report.meta_ads.reporting_timezone == "Asia/Shanghai"
