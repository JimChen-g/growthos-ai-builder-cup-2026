"use client";

import type { IntelligenceHistory, IntelligenceSnapshot } from "../lib/api";
import { asNumber } from "../lib/api";
import { useI18n } from "../lib/i18n";

function hasMeasurement(snapshot: IntelligenceSnapshot): boolean {
  if (snapshot.kind !== "brand_visibility") return true;
  const result = snapshot.result;
  return "providers" in result && (
    result.providers.some((provider) => provider.successful_prompts > 0) ||
    result.results.some((answer) => !answer.error)
  );
}

export function HistoryList({ history, title }: { history: IntelligenceHistory; title: string }) {
  const { t, tx, locale } = useI18n();
  if (history.items.length === 0) return null;
  const time = new Intl.DateTimeFormat(locale, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return (
    <section className="section">
      <div className="section-head">
        <h2>{title}</h2>
      </div>
      <ol className="rows">
        {history.items.slice(0, 6).map((item) => {
          const isLiveFeedback = item.snapshot.kind === "customer_intelligence" && item.snapshot.mode === "live";
          const liveFeedback = isLiveFeedback && "signals" in item.snapshot.result ? item.snapshot.result : null;
          const count = liveFeedback?.analysis?.sample_count ?? liveFeedback?.signals.length;
          return (
            <li key={item.snapshot.snapshot_id}>
              <span className="index wide">{isLiveFeedback ? count ?? "—" : item.snapshot.score == null || !hasMeasurement(item.snapshot) ? "—" : asNumber(item.snapshot.score).toFixed(0)}</span>
              <div className="row-main">
                <strong>{item.snapshot.brand}</strong>
                <small>{time.format(new Date(item.snapshot.generated_at))}</small>
                {isLiveFeedback ? <>
                  <small>{t("customers.searchResults", { n: count ?? "—" })} · {t("customers.sourceBreakdown", {
                    x: liveFeedback?.sources.find((source) => source.provider === "x")?.signal_count ?? "—",
                    reddit: liveFeedback?.sources.find((source) => source.provider === "reddit")?.signal_count ?? "—",
                  })}</small>
                  <p>{t("customers.historyUnverified")}</p>
                </> : <p>{hasMeasurement(item.snapshot) ? tx(item.snapshot.summary) : t("visibility.unavailable")}</p>}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
