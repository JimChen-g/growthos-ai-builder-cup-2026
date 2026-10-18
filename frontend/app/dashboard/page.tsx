"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ActionLedger, StatusPill } from "../../components/ActionLedger";
import { useDataMode } from "../../components/DataModeContext";
import { PageState } from "../../components/PageState";
import {
  asNumber,
  ApiError,
  getJson,
  requestJson,
  type AgentActionLedger,
  type AuditIntegrityStatus,
  type ConnectionCenter,
  type GrowthDashboard,
  type LiveGrowthIntelligence,
} from "../../lib/api";
import { type MessageKey, useI18n } from "../../lib/i18n";
import { formatMoney, formatTimestamp } from "../../lib/reporting";

export default function GrowthPage() {
  const { mode, capabilities } = useDataMode();
  const [dashboard, setDashboard] = useState<GrowthDashboard | null>(null);
  const [ledger, setLedger] = useState<AgentActionLedger | null>(null);
  const [integrity, setIntegrity] = useState<AuditIntegrityStatus | null>(null);
  const [failed, setFailed] = useState(false);

  const loadRecord = () =>
    Promise.all([
      getJson<AgentActionLedger>(`/api/v1/audit-log?mode=${mode}&limit=50`),
      getJson<AuditIntegrityStatus>(`/api/v1/audit-proof/status?mode=${mode}`),
    ]).then(([nextLedger, nextIntegrity]) => {
      setLedger(nextLedger);
      setIntegrity(nextIntegrity);
    });

  useEffect(() => {
    if (mode === "live") return;
    setLedger(null);
    setIntegrity(null);
    setFailed(false);
    const requests: Array<Promise<unknown>> = capabilities.public_demo_only && mode === "demo" ? [] : [loadRecord()];
    if (mode === "demo") requests.push(getJson<GrowthDashboard>("/api/v1/dashboard").then(setDashboard));
    Promise.all(requests).catch(() => setFailed(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, capabilities.public_demo_only]);

  if (mode === "live") return <LiveGrowth />;
  if (failed) return <PageState error />;
  if ((!capabilities.public_demo_only && (!ledger || !integrity)) || !dashboard) return <PageState />;
  return <DemoGrowth dashboard={dashboard!} ledger={ledger} integrity={integrity} />;
}

function DemoGrowth({
  dashboard,
  ledger,
  integrity,
}: {
  dashboard: GrowthDashboard;
  ledger: AgentActionLedger | null;
  integrity: AuditIntegrityStatus | null;
}) {
  const { t, tx } = useI18n();
  const { capabilities } = useDataMode();
  return (
    <main className="page">
      <h1>{t("growth.title")}</h1>
      <p className="lead">{t("growth.subtitle")}</p>
      <p className="note">{t("sample.case")}</p>

      <dl className="metrics">
        {dashboard.metrics.map((metric) => (
          <div key={metric.key} className={`metric tone-${metric.tone}`}>
            <dt>{tx(metric.label)}</dt>
            <dd>{metric.formatted_value}</dd>
            <small>{tx(metric.trend)}</small>
          </div>
        ))}
      </dl>

      <section className="section" aria-labelledby="plan-heading">
        <div className="section-head">
          <h2 id="plan-heading">{t("growth.plan")}</h2>
        </div>
        <ol className="rows plan">
          {dashboard.recommendations.map((item) => (
            <li key={`${item.source_agent}-${item.priority}`}>
              <span className="index">{item.priority}</span>
              <div className="row-main">
                <strong>{tx(item.action)}</strong>
                <small>{tx(item.source_agent)}</small>
                <p>{tx(item.rationale)}</p>
              </div>
              {capabilities.public_demo_only && item.workflow_run_id ? (
                <div className="row-side">
                  <Link className="link" href="/builder-cup">{t("nav.builderCup")} →</Link>
                </div>
              ) : item.workflow_run_id && item.approval_status && (
                <div className="row-side">
                  <StatusPill status={item.approval_status} />
                  <Link className="link" href={`/agent?runId=${encodeURIComponent(item.workflow_run_id)}`}>
                    {item.approval_status === "pending" ? t("growth.review") : t("growth.view")} →
                  </Link>
                </div>
              )}
            </li>
          ))}
        </ol>
      </section>

      {ledger && integrity && <ActionLedger ledger={ledger} integrity={integrity} />}
    </main>
  );
}

const BUSINESS_SOURCES = [
  { id: "stripe", name: "Stripe" }, { id: "google_ads", name: "Google Ads" }, { id: "meta_ads", name: "Meta Ads" },
];

function liveError(caught: unknown): MessageKey {
  if (caught instanceof ApiError) {
    if ([401, 403].includes(caught.status)) return "live.authRequired";
    if (caught.message.includes("requires matching Google Ads and Meta Ads currencies")) return "growth.live.currencyMismatch";
    if (caught.message.includes("account currency does not match")) return "connections.currencyMismatch";
    if (caught.message.includes("reporting timezone")) return "growth.live.timezoneUnavailable";
    if (caught.status === 404) return "live.serviceUnavailable";
    if (caught.status === 409) return "growth.live.configurationIssue";
    if (caught.status === 429) return "live.quota";
  }
  return "live.requestError";
}

function LiveGrowth() {
  const { t, locale } = useI18n();
  const [report, setReport] = useState<LiveGrowthIntelligence | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<MessageKey | null>(null);
  const [center, setCenter] = useState<ConnectionCenter | null>(null);
  const [readinessLoading, setReadinessLoading] = useState(true);
  const [readinessError, setReadinessError] = useState<MessageKey | null>(null);
  const [ledger, setLedger] = useState<AgentActionLedger | null>(null);
  const [integrity, setIntegrity] = useState<AuditIntegrityStatus | null>(null);
  const [auditError, setAuditError] = useState(false);
  const [auditBusy, setAuditBusy] = useState(false);

  async function refreshReadiness(signal?: AbortSignal) {
    setReadinessLoading(true);
    setReadinessError(null);
    try {
      const next = await requestJson<ConnectionCenter>("/api/v1/connections", { signal });
      if (!signal?.aborted) setCenter(next);
    } catch (caught) {
      if (!signal?.aborted) { setCenter(null); setReadinessError(liveError(caught)); }
    } finally {
      if (!signal?.aborted) setReadinessLoading(false);
    }
  }

  async function refreshAudit(signal?: AbortSignal) {
    setAuditBusy(true);
    setAuditError(false);
    try {
      const [nextLedger, nextIntegrity] = await Promise.all([
        requestJson<AgentActionLedger>("/api/v1/audit-log?mode=live&limit=50", { signal }),
        requestJson<AuditIntegrityStatus>("/api/v1/audit-proof/status?mode=live", { signal }),
      ]);
      if (!signal?.aborted) { setLedger(nextLedger); setIntegrity(nextIntegrity); }
    } catch {
      if (!signal?.aborted) setAuditError(true);
    } finally {
      if (!signal?.aborted) setAuditBusy(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void refreshReadiness(controller.signal);
    void refreshAudit(controller.signal);
    return () => controller.abort();
  }, []);

  const missing = BUSINESS_SOURCES.filter((source) => !center?.connections.some((item) => item.provider === source.id && item.configured));
  const googleCurrency = center?.connections.find((item) => item.provider === "google_ads")?.currency;
  const metaCurrency = center?.connections.find((item) => item.provider === "meta_ads")?.currency;
  const adsCurrencyMismatch = Boolean(googleCurrency && metaCurrency && googleCurrency !== metaCurrency);
  const ready = Boolean(center) && !readinessLoading && !readinessError && missing.length === 0 && !adsCurrencyMismatch;

  async function load() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      setReport(await requestJson<LiveGrowthIntelligence>("/api/v1/growth-intelligence/live", { method: "POST" }));
      // The report is valid even if the independent history refresh is unavailable.
      void refreshAudit();
    } catch (caught) {
      setError(liveError(caught));
      if (caught instanceof ApiError && caught.status === 409) void refreshReadiness();
    } finally {
      setBusy(false);
    }
  }

  const money = (value: number | string | null, currency: string) => formatMoney(value, currency, locale);
  const mrrWarnings = [...new Set(report?.revenue.mrr_warnings ?? [])];

  return (
    <main className="page">
      <h1>{t("growth.title")}</h1>
      <p className="lead">{t("growth.live.subtitle")}</p>
      <section className="section" aria-label={t("connections.business")}>
        <p>{readinessLoading ? t("common.loading") : center ? t("home.live.progress", { n: 3 - missing.length }) : t(readinessError ?? "live.requestError")}</p>
        {center && missing.length > 0 && <p className="note warn">{t("growth.live.missingSources", { sources: missing.map((item) => item.name).join(" · ") })}</p>}
        {adsCurrencyMismatch && <p className="note warn">{t("growth.live.currencyMismatch")}</p>}
        <p className="note">{t("growth.live.configurationOnly")}</p>
        <div className="actions-row">
          <Link className="link" href="/connections">{t("common.manage")}</Link>
          <button className="link" disabled={readinessLoading || busy} type="button" onClick={() => { void refreshReadiness(); }}>{t("growth.live.refreshSetup")}</button>
        </div>
      </section>
      <div className="actions-row">
        <button className="button" disabled={busy || !ready} onClick={load} type="button">
          {busy ? t("growth.live.loading") : report ? t("growth.live.refresh") : t("growth.live.load")}
        </button>
        {error && <span className="note warn" role="alert">{t(error)}</span>}
      </div>

      {report ? (
        <>
          <p className="note">{t("environment.hint")}</p>
          {[report.revenue, report.google_ads, report.meta_ads].some((source) => source.account_environment === "test") && <p className="note warn" role="status">{t("environment.testReport")}</p>}
          <dl className="metrics">
            <div className="metric">
              <dt>{t("growth.live.score")}</dt>
              <dd>{asNumber(report.growth_score.score).toFixed(0)}/100</dd>
              <small>{t(`band.${report.growth_score.band}` as MessageKey)}</small>
            </div>
            <div className="metric">
              <dt>{t("growth.live.revenue")}</dt>
              <dd>{report.revenue.mrr_status === "incomplete" ? "—" : money(report.revenue.mrr, report.revenue.currency)}</dd>
              <small>{report.revenue.mrr == null || report.revenue.mrr_status === "incomplete" ? t("growth.live.mrrIncomplete") : "Stripe"}</small>
              <small>{t("growth.live.mrrAsOf", { time: formatTimestamp(report.revenue.mrr_as_of, locale) })}</small>
            </div>
            <div className="metric">
              <dt>Google Ads</dt>
              <dd>{report.google_ads.roas == null ? "—" : `${asNumber(report.google_ads.roas).toFixed(1)}x ROAS`}</dd>
              <small>{money(report.google_ads.spend, report.google_ads.currency)}</small>
            </div>
            <div className="metric">
              <dt>Meta Ads</dt>
              <dd>{report.meta_ads.roas == null ? "—" : `${asNumber(report.meta_ads.roas).toFixed(1)}x ROAS`}</dd>
              <small>{money(report.meta_ads.spend, report.meta_ads.currency)}</small>
            </div>
          </dl>
          <p className="note">
            {error && <>{t("growth.live.previousReport")} </>}
            {t("growth.live.assumed")} ·{" "}
            {t("growth.live.updated", {
              time: formatTimestamp(report.generated_at, locale),
            })}
          </p>
          <p className="note">{t("growth.live.currencyNote")}</p>
          <p className="note">{t("growth.live.fractionalAmounts")}</p>
          <section className="section" aria-label={t("growth.live.sourceDetails")}>
            <h2>{t("growth.live.sourceDetails")}</h2>
            <p className="note">{t(report.period_policy === "common_complete_calendar_days" ? "growth.live.periodPolicy" : "growth.live.periodUnknown")}</p>
            <dl className="facts stacked">
              {([{ name: "Stripe", source: report.revenue }, { name: "Google Ads", source: report.google_ads }, { name: "Meta Ads", source: report.meta_ads }]).map(({ name, source }) => <div key={name}>
                <dt>{name}</dt>
                <dd>{t(`environment.${source.account_environment ?? "unknown"}` as MessageKey)}<small>{t("growth.live.period", { start: source.period?.start ?? "—", end: source.period?.end ?? "—" })}</small><small>{t("growth.live.reportingTimezone", { timezone: source.reporting_timezone ?? t("growth.live.unknownTimezone") })}</small><small>{t("growth.live.collectedAt", { time: formatTimestamp(source.collected_at, locale) })}</small></dd>
              </div>)}
            </dl>
            <p className="note">{t("growth.live.mrrSnapshot")}</p>
            <p className="note">{t("growth.live.periodRevenue", { amount: money(report.revenue.revenue, report.revenue.currency) })}</p>
            <p className="note">{t(report.revenue.mrr_policy === "net_recurring_v1" ? "growth.live.mrrPolicy" : "growth.live.mrrPolicyUnknown")}</p>
            {mrrWarnings.length > 0 && <ul className="plain note warn">{mrrWarnings.map((warning) => <li key={warning}>{t(MRR_WARNING_KEYS[warning] ?? "growth.live.mrrWarningUnknown")}</li>)}</ul>}
          </section>
          {report.audit_recorded === false && <p className="note warn" role="status">{t("growth.live.auditNotSaved")}</p>}
        </>
      ) : (
        !error && <p className="muted quiet-block">{t("growth.live.empty")}</p>
      )}

      {auditError && <div className="section" role="status">
        <p className="note warn">{t("growth.live.auditUnavailable")}</p>
        <button className="link" disabled={auditBusy} type="button" onClick={() => { void refreshAudit(); }}>{t("growth.live.retryAudit")}</button>
      </div>}
      {ledger && integrity && <ActionLedger ledger={ledger} integrity={integrity} />}
    </main>
  );
}

const MRR_WARNING_KEYS: Record<string, MessageKey> = {
  mrr_subscriptions_unavailable: "growth.live.mrrWarningSubscriptions",
  mrr_items_incomplete: "growth.live.mrrWarningItems",
  mrr_tiered_unsupported: "growth.live.mrrWarningTiered",
  mrr_discount_unexpanded: "growth.live.mrrWarningDiscountDetails",
  mrr_discount_unsupported: "growth.live.mrrWarningDiscount",
  mrr_temporary_discount_unsupported: "growth.live.mrrWarningTemporaryDiscount",
  mrr_price_unsupported: "growth.live.mrrWarningPrice",
  mrr_tax_inclusive_unsupported: "growth.live.mrrWarningTax",
  mrr_customer_discount_unavailable: "growth.live.mrrWarningCustomerDiscount",
  mrr_discount_lookup_limit: "growth.live.mrrWarningLookupLimit",
  mrr_discount_unavailable: "growth.live.mrrWarningDiscountUnavailable",
};
