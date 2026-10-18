"use client";

import { useEffect, useState } from "react";

import { useDataMode } from "../../components/DataModeContext";
import { HistoryList } from "../../components/HistoryList";
import { PageState } from "../../components/PageState";
import {
  asNumber,
  getJson,
  requestJson,
  type FeedbackAnalysis,
  type FeedbackCollectionResponse,
  type IntelligenceHistory,
} from "../../lib/api";
import { useI18n } from "../../lib/i18n";

export default function CustomersPage() {
  const { mode } = useDataMode();
  const { t } = useI18n();
  const [analysis, setAnalysis] = useState<FeedbackAnalysis | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (mode !== "demo") return;
    setAnalysis(null);
    getJson<FeedbackAnalysis>("/api/v1/feedback/demo").then(setAnalysis).catch(() => setFailed(true));
  }, [mode]);

  if (mode === "live") return <LiveCustomers />;
  if (failed) return <PageState error />;
  if (!analysis) return <PageState />;
  return (
    <main className="page">
      <h1>{t("customers.title")}</h1>
      <p className="lead">{t("customers.subtitle")}</p>
      <Insight analysis={analysis} commentsTitle={t("customers.comments")} localizeSample />
    </main>
  );
}

function Insight({ analysis, commentsTitle, localizeSample = false, liveResults = false }: {
  analysis: FeedbackAnalysis;
  commentsTitle: string;
  localizeSample?: boolean;
  liveResults?: boolean;
}) {
  const { t, tx } = useI18n();
  return (
    <>
      <section className="hero">
        <div className="hero-number">
          <span>{asNumber(analysis.sentiment_score).toFixed(0)}</span>
          <small>/100</small>
        </div>
        <div className="hero-copy">
          <p className="eyebrow">
            {t("customers.sentiment")} · {tx(analysis.overall_sentiment)} ·{" "}
            {t("customers.positive", { n: asNumber(analysis.positive_pct).toFixed(0) })}
          </p>
          <p className="eyebrow">{t("customers.complaint")}</p>
          <h2>{tx(analysis.main_problem)}</h2>
        </div>
      </section>

      <section className="focus">
        <p className="eyebrow">{t("customers.suggestion")}</p>
        <p className="lead">{tx(analysis.recommendation)}</p>
      </section>

      <section className="section">
        <div className="section-head">
          <h2>{commentsTitle}</h2>
          <span className="muted">{t(liveResults ? "customers.searchResults" : "customers.signals", { n: analysis.sample_count })}</span>
        </div>
        <ul className="quotes">
          {analysis.analyzed_signals.map((item, index) => (
            <li key={`${item.source}-${index}`}>
              <span className={`dot sentiment-${item.sentiment}`} title={tx(item.sentiment)} aria-hidden="true" />
              <blockquote>{localizeSample ? tx(item.text) : item.text}</blockquote>
              <small>
                {item.source === "x" ? "X" : "Reddit"} · {tx(item.sentiment)}
                {item.topics.length > 0 && ` · ${item.topics.map((topic) => tx(topic)).join("、")}`}
              </small>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

function LiveCustomers() {
  const { t, tx } = useI18n();
  const [brand, setBrand] = useState("");
  const [collection, setCollection] = useState<FeedbackCollectionResponse | null>(null);
  const [history, setHistory] = useState<IntelligenceHistory | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refreshHistory = () =>
    getJson<IntelligenceHistory>("/api/v1/intelligence-history?mode=live&kind=customer_intelligence&limit=20").then(setHistory);

  useEffect(() => {
    refreshHistory().catch(() => undefined);
  }, []);

  async function collect() {
    setBusy(true);
    setError("");
    try {
      const result = await requestJson<FeedbackCollectionResponse>("/api/v1/feedback/collect", {
        method: "POST",
        body: JSON.stringify({ brand: brand.trim(), mode: "live", sources: ["reddit", "x"] }),
      });
      setCollection(result);
      await refreshHistory();
    } catch {
      setError(t("live.requestError"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page">
      <h1>{t("customers.title")}</h1>
      <p className="lead">{t("customers.subtitle")}</p>
      <p className="note warn">{t("customers.liveSearchCaveat")}</p>
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (brand.trim() && !busy) collect();
        }}
      >
        <label className="field">
          <span>{t("customers.brand")}</span>
          <input id="customer-brand" onChange={(event) => setBrand(event.target.value)} placeholder="Intercom" value={brand} />
        </label>
        <button className="button" disabled={busy || !brand.trim()} type="submit">
          {busy ? t("customers.collecting") : t("customers.collect")}
        </button>
      </form>
      {error && <p className="note warn">{error}</p>}

      {collection && (
        <section className="section">
          <div className="section-head">
            <h2>{t("customers.sources")}</h2>
          </div>
          <ul className="rows">
            {collection.sources.map((source) => (
              <li key={source.provider}>
                <span className={`dot ${source.status === "completed" ? "ok" : "warn"}`} aria-hidden="true" />
                <div className="row-main">
                  <strong>{source.provider === "x" ? "X" : "Reddit"}</strong>
                  <small>{tx(source.collection_method)} · {t("customers.searchResults", { n: source.signal_count })}</small>
                  {source.error && <p>{t("live.sourceError")}</p>}
                </div>
              </li>
            ))}
          </ul>
          {collection.analysis ? (
            <Insight analysis={collection.analysis} commentsTitle={t("customers.liveComments")} liveResults />
          ) : (
            <p className="muted">{t("customers.liveNone")}</p>
          )}
        </section>
      )}
      {history && <HistoryList history={history} title={t("visibility.history")} />}
    </main>
  );
}
