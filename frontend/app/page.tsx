"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useDataMode } from "../components/DataModeContext";
import { PageState } from "../components/PageState";
import { asNumber, getJson, signedPercent, type ConnectionCenter, type FounderExperience } from "../lib/api";
import { type MessageKey, useI18n } from "../lib/i18n";

function greetingKey(): MessageKey {
  const hour = new Date().getHours();
  if (hour < 12) return "greeting.morning";
  if (hour < 18) return "greeting.afternoon";
  return "greeting.evening";
}

export default function TodayPage() {
  const { mode, capabilities } = useDataMode();
  const { t, tx, locale } = useI18n();
  const [experience, setExperience] = useState<FounderExperience | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (mode !== "demo") return;
    getJson<FounderExperience>("/api/v1/founder/experience")
      .then(setExperience)
      .catch(() => setFailed(true));
  }, [mode]);

  if (mode === "live") return <LiveToday />;
  if (failed) return <PageState error />;
  if (!experience) return <PageState />;

  const { briefing, dashboard } = experience;
  const health = asNumber(briefing.growth_health);
  const plan = dashboard.recommendations.find((item) => item.priority === 1) ?? dashboard.recommendations[0];
  const pendingRun = dashboard.recommendations.find((item) => item.approval_status === "pending");
  const today = new Intl.DateTimeFormat(locale, { weekday: "long", month: "long", day: "numeric" }).format(new Date());

  return (
    <main className="page">
      <p className="eyebrow">{today}</p>
      <h1>{t(greetingKey())}</h1>

      <section className="hero" aria-label={t("home.health")}>
        <div className="hero-number">
          <span>{health.toFixed(0)}</span>
          <small>/100</small>
        </div>
        <div className="hero-copy">
          <p className="eyebrow">
            {t("home.health")} · {t(`band.${briefing.health_band}` as MessageKey)}
          </p>
          <p className="lead">{tx(briefing.executive_summary)}</p>
        </div>
      </section>

      <dl className="stats">
        {briefing.yesterday.map((signal) => (
          <div key={signal.key}>
            <dt>{tx(signal.label)}</dt>
            <dd>{signal.current_value}</dd>
            <small className={asNumber(signal.change_pct) >= 0 ? "up" : "down"}>
              {tx(signal.context)} {signedPercent(signal.change_pct)}
            </small>
          </div>
        ))}
      </dl>

      <section className="focus">
        <p className="eyebrow">{t("home.oneThing")}</p>
        <h2>{tx(briefing.top_recommendation)}</h2>
        <p>{tx(plan?.rationale ?? briefing.recommendation_reason)}</p>
        <div className="actions-row">
          <Link className="button" href="/dashboard">{t("home.seePlan")}</Link>
          {capabilities.public_demo_only ? (
            <Link className="link" href="/builder-cup">{t("nav.builderCup")} →</Link>
          ) : pendingRun?.workflow_run_id && (
            <Link className="link" href={`/agent?runId=${encodeURIComponent(pendingRun.workflow_run_id)}`}>
              {t("home.pending")} →
            </Link>
          )}
        </div>
      </section>
    </main>
  );
}

function LiveToday() {
  const { t } = useI18n();
  const [center, setCenter] = useState<ConnectionCenter | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    getJson<ConnectionCenter>("/api/v1/connections").then(setCenter).catch(() => setFailed(true));
  }, []);

  if (failed) return <PageState error />;
  if (!center) return <PageState />;
  const ready = center.connections.filter(
    (item) => ["stripe", "google_ads", "meta_ads"].includes(item.provider) && item.configured,
  ).length;

  return (
    <main className="page">
      <h1>{ready === 3 ? t("home.live.readyTitle") : t("home.live.title")}</h1>
      <p className="lead">{t("home.live.body")}</p>
      <p className="note">{t("home.live.configurationOnly")}</p>
      <div className="progress" aria-label={t("home.live.progress", { n: ready })}>
        {[0, 1, 2].map((index) => (
          <span className={index < ready ? "on" : undefined} key={index} />
        ))}
        <small>{t("home.live.progress", { n: ready })}</small>
      </div>
      <div className="actions-row">
        <Link className="button" href={ready === 3 ? "/dashboard" : "/connections"}>
          {ready === 3 ? t("home.live.readyCta") : t("home.live.cta")}
        </Link>
      </div>
    </main>
  );
}
