"use client";

import { useEffect, useMemo, useState } from "react";

import { useDataMode } from "../../components/DataModeContext";
import { HistoryList } from "../../components/HistoryList";
import { PageState } from "../../components/PageState";
import {
  asNumber,
  getJson,
  requestJson,
  type ConnectionCenter,
  type ConnectorProvider,
  type GeoScanReport,
  type GrowthDashboard,
  type IntelligenceHistory,
} from "../../lib/api";
import { useI18n } from "../../lib/i18n";

const providerNames: Partial<Record<ConnectorProvider, string>> = {
  openai: "ChatGPT",
  gemini: "Gemini",
  gemini_vertex: "Gemini",
  perplexity: "Perplexity",
};

export default function VisibilityPage() {
  const { mode } = useDataMode();
  const { t, tx } = useI18n();
  const [dashboard, setDashboard] = useState<GrowthDashboard | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (mode !== "demo") return;
    const controller = new AbortController();
    setFailed(false);
    setDashboard(null);
    requestJson<GrowthDashboard>("/api/v1/dashboard", { signal: controller.signal })
      .then(setDashboard)
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [mode]);

  if (mode === "live") return <LiveVisibility />;
  if (failed) return <PageState error />;
  if (!dashboard) return <PageState />;
  const metric = dashboard.metrics.find((item) => item.key === "ai_visibility");
  const plan = dashboard.recommendations.find((item) => item.source_agent === "GEO Agent");

  return (
    <main className="page">
      <h1>{t("visibility.title")}</h1>
      <p className="lead">{t("visibility.subtitle")}</p>
      <section className="hero">
        <div className="hero-number">
          <span>{metric ? asNumber(metric.value).toFixed(0) : "—"}</span>
          <small>/100</small>
        </div>
        <div className="hero-copy">
          <p className="eyebrow">{t("visibility.demoScore")}</p>
          <p className="lead">{t("visibility.demo")}</p>
        </div>
      </section>
      {plan && (
        <section className="focus">
          <p className="eyebrow">{t("home.oneThing")}</p>
          <h2>{tx(plan.action)}</h2>
          <p>{tx(plan.rationale)}</p>
        </section>
      )}
      <p className="muted">{t("visibility.demoHint")}</p>
    </main>
  );
}

function LiveVisibility() {
  const { t, tx } = useI18n();
  const [center, setCenter] = useState<ConnectionCenter | null>(null);
  const [history, setHistory] = useState<IntelligenceHistory | null>(null);
  const [brand, setBrand] = useState("");
  const [website, setWebsite] = useState("");
  const [category, setCategory] = useState("");
  const [competitors, setCompetitors] = useState("");
  const [report, setReport] = useState<GeoScanReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refreshHistory = () =>
    getJson<IntelligenceHistory>("/api/v1/intelligence-history?mode=live&kind=brand_visibility&limit=20").then(setHistory);

  useEffect(() => {
    Promise.all([getJson<ConnectionCenter>("/api/v1/connections").then(setCenter), refreshHistory()]).catch(() =>
      setError(t("common.error")),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const providers = useMemo<ConnectorProvider[]>(() => {
    const configured = (provider: ConnectorProvider) =>
      Boolean(center?.connections.some((item) => item.provider === provider && item.configured));
    const gemini: ConnectorProvider[] = configured("gemini_vertex") ? ["gemini_vertex"] : configured("gemini") ? ["gemini"] : [];
    return [...(configured("openai") ? (["openai"] as ConnectorProvider[]) : []), ...gemini, ...(configured("perplexity") ? (["perplexity"] as ConnectorProvider[]) : [])];
  }, [center]);

  async function run() {
    setBusy(true);
    setError("");
    try {
      const result = await requestJson<GeoScanReport>("/api/v1/geo/scans", {
        method: "POST",
        body: JSON.stringify({
          brand,
          website,
          category,
          competitors: competitors.split(/[,，、]/).map((item) => item.trim()).filter(Boolean),
          providers,
          mode: "live",
        }),
      });
      setReport(result);
      await refreshHistory();
    } catch (caught) {
      setError(caught instanceof Error ? tx(caught.message) : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  if (!center && !error) return <PageState />;
  const ready = providers.length > 0 && brand.trim() && website.trim() && category.trim();
  const successfulPrompts = report?.providers.reduce((total, item) => total + item.successful_prompts, 0) ?? 0;
  const attemptedPrompts = report?.providers.reduce((total, item) => total + item.attempted_prompts, 0) ?? 0;
  const hasMeasurement = successfulPrompts > 0;

  return (
    <main className="page">
      <h1>{t("visibility.title")}</h1>
      <p className="lead">{t("visibility.subtitle")}</p>
      <form
        className="stack-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (ready && !busy) run();
        }}
      >
        <label className="field"><span>{t("visibility.brand")}</span><input id="geo-brand" onChange={(e) => setBrand(e.target.value)} placeholder="Intercom" value={brand} /></label>
        <label className="field"><span>{t("visibility.website")}</span><input id="geo-website" onChange={(e) => setWebsite(e.target.value)} placeholder="https://www.intercom.com" value={website} /></label>
        <label className="field"><span>{t("visibility.category")}</span><input id="geo-category" onChange={(e) => setCategory(e.target.value)} placeholder="AI customer support" value={category} /></label>
        <label className="field"><span>{t("visibility.competitors")}</span><input id="geo-competitors" onChange={(e) => setCompetitors(e.target.value)} placeholder="Zendesk, Ada" value={competitors} /></label>
        <div className="actions-row">
          <button className="button" disabled={!ready || busy} type="submit">
            {busy ? t("visibility.running") : t("visibility.run")}
          </button>
          <span className="note">
            {providers.length ? providers.map((item) => providerNames[item]).join(" · ") : t("visibility.noSources")}
          </span>
        </div>
      </form>
      {error && <p className="note warn">{error}</p>}

      {report && (
        <section className="section" aria-live="polite">
          <div className="hero compact">
            <div className="hero-number">
              <span>{hasMeasurement ? asNumber(report.overall_visibility_score).toFixed(0) : "—"}</span>
              {hasMeasurement && <small>/100</small>}
            </div>
            <p className="eyebrow">{t(hasMeasurement ? "visibility.score" : "visibility.unavailable")} · {report.brand}</p>
          </div>
          <p className={`note${successfulPrompts < attemptedPrompts ? " warn" : ""}`}>
            {hasMeasurement
              ? t("visibility.coverage", { n: successfulPrompts, total: attemptedPrompts })
              : t("visibility.noResult")}
          </p>
          <ul className="rows">
            {report.providers.map((item) => (
              <li key={item.provider}>
                <span className="index wide">{item.successful_prompts > 0 ? asNumber(item.visibility_score).toFixed(0) : "—"}</span>
                <div className="row-main">
                  <strong>{providerNames[item.provider] ?? item.provider}</strong>
                  <small>
                    {item.successful_prompts > 0
                      ? t("visibility.mentions", { m: item.brand_mentions, n: item.successful_prompts })
                      : t("visibility.unavailable")}
                  </small>
                  {item.successful_prompts < item.attempted_prompts && (
                    <small className="warn">{t("visibility.coverage", { n: item.successful_prompts, total: item.attempted_prompts })}</small>
                  )}
                </div>
              </li>
            ))}
          </ul>
          <details>
            <summary>{t("common.details")}</summary>
            <ul className="plain">
              {report.results.map((result, index) => (
                <li key={`${result.provider}-${index}`}>
                  <strong>{result.prompt}</strong>
                  <small>{providerNames[result.provider]}</small>
                  <p>{result.error ? tx(result.error) : result.answer}</p>
                  {result.citations.length > 0 && (
                    <ul className="plain" aria-label={t("visibility.citations")}>
                      {result.citations.map((citation) => (
                        <li key={citation.url}>
                          <a className="link" href={citation.url} rel="noopener noreferrer" target="_blank">
                            {citation.title} ↗
                          </a>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </details>
        </section>
      )}
      {history && <HistoryList history={history} title={t("visibility.history")} />}
    </main>
  );
}
