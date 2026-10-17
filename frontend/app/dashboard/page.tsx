"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ActionLedger, StatusPill } from "../../components/ActionLedger";
import { useDataMode } from "../../components/DataModeContext";
import { PageState } from "../../components/PageState";
import {
  asNumber,
  getJson,
  requestJson,
  type AgentActionLedger,
  type AuditIntegrityStatus,
  type GrowthDashboard,
  type LiveGrowthIntelligence,
} from "../../lib/api";
import { type MessageKey, useI18n } from "../../lib/i18n";

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
    setLedger(null);
    setIntegrity(null);
    setFailed(false);
    const requests: Array<Promise<unknown>> = capabilities.public_demo_only ? [] : [loadRecord()];
    if (mode === "demo") requests.push(getJson<GrowthDashboard>("/api/v1/dashboard").then(setDashboard));
    Promise.all(requests).catch(() => setFailed(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, capabilities.public_demo_only]);

  if (failed) return <PageState error />;
  if ((!capabilities.public_demo_only && (!ledger || !integrity)) || (mode === "demo" && !dashboard)) return <PageState />;
  if (mode === "live") return <LiveGrowth ledger={ledger!} integrity={integrity!} onRecorded={loadRecord} />;
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

function LiveGrowth({
  ledger,
  integrity,
  onRecorded,
}: {
  ledger: AgentActionLedger;
  integrity: AuditIntegrityStatus;
  onRecorded: () => Promise<void>;
}) {
  const { t, tx, locale } = useI18n();
  const [report, setReport] = useState<LiveGrowthIntelligence | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setBusy(true);
    setError("");
    try {
      setReport(await requestJson<LiveGrowthIntelligence>("/api/v1/growth-intelligence/live", { method: "POST" }));
      await onRecorded();
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : "";
      setError(detail.includes("missing configuration") ? t("growth.live.missing") : tx(detail) || t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  const money = (value: number | string, currency: string) =>
    new Intl.NumberFormat(locale, { style: "currency", currency: currency.toUpperCase(), maximumFractionDigits: 0 }).format(asNumber(value));

  return (
    <main className="page">
      <h1>{t("growth.title")}</h1>
      <p className="lead">{t("growth.live.subtitle")}</p>
      <div className="actions-row">
        <button className="button" disabled={busy} onClick={load} type="button">
          {busy ? t("growth.live.loading") : report ? t("growth.live.refresh") : t("growth.live.load")}
        </button>
        {error && <span className="note warn">{error}</span>}
      </div>

      {report ? (
        <>
          <dl className="metrics">
            <div className="metric">
              <dt>{t("growth.live.score")}</dt>
              <dd>{asNumber(report.growth_score.score).toFixed(0)}/100</dd>
              <small>{t(`band.${report.growth_score.band}` as MessageKey)}</small>
            </div>
            <div className="metric">
              <dt>{t("growth.live.revenue")}</dt>
              <dd>{money(report.revenue.mrr, report.revenue.currency)}</dd>
              <small>Stripe</small>
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
            {t("growth.live.assumed")} ·{" "}
            {t("growth.live.updated", {
              time: new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(new Date(report.generated_at)),
            })}
          </p>
        </>
      ) : (
        !error && <p className="muted quiet-block">{t("growth.live.empty")}</p>
      )}

      <ActionLedger ledger={ledger} integrity={integrity} />
    </main>
  );
}
