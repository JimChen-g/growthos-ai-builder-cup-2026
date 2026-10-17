"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ActionLedger } from "../../components/ActionLedger";
import { useDataMode } from "../../components/DataModeContext";
import {
  asNumber,
  getJson,
  requestJson,
  type AgentActionLedger,
  type AuditIntegrityStatus,
} from "../../lib/api";
import { useI18n } from "../../lib/i18n";

type WorkflowRun = {
  run_id: string;
  status: "awaiting_approval" | "rejected" | "completed" | "failed";
  narrative: {
    founder_message: string;
    acknowledgement: string;
    top_problems: Array<{ platform: string; problem: string; reason: string; evidence: string[] }>;
    recommended_actions: string[];
    expected_impact: {
      expected_roas: string;
      budget: string;
      currency: string;
      measurement_window_days: number;
      disclaimer: string;
    };
    approval_prompt: string;
  };
  approval: {
    approval_id: string;
    action_digest: string;
    status: "pending" | "approved" | "rejected" | "executing" | "executed" | "failed";
  };
  campaign_draft: {
    campaign_name: string;
    audience: string;
    keywords: string[];
    headlines: string[];
    descriptions: string[];
  };
  geo_analysis: {
    brand_status: string;
    visibility_score: number;
    action_plan: Array<{ priority: number; action: string; rationale: string }>;
  };
  execution: { campaign_resource_name: string } | null;
};

export default function ActionsPage() {
  const { capabilities, mode } = useDataMode();
  const { t } = useI18n();

  if (capabilities.public_demo_only && mode === "demo") {
    return (
      <main className="page">
        <h1>{t("actions.title")}</h1>
        <p className="lead">{t("actions.public.title")}</p>
        <p className="muted">{t("actions.public.body")}</p>
        <div className="actions-row">
          <Link className="button" href="/builder-cup">{t("nav.builderCup")}</Link>
        </div>
      </main>
    );
  }
  return <PrivateActionsPage />;
}

function PrivateActionsPage() {
  const { mode } = useDataMode();
  const { t, tx, locale, lang } = useI18n();
  const [message, setMessage] = useState(t("actions.defaultMessage"));
  const [edited, setEdited] = useState(false);
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [ledger, setLedger] = useState<AgentActionLedger | null>(null);
  const [integrity, setIntegrity] = useState<AuditIntegrityStatus | null>(null);

  useEffect(() => {
    if (!edited) setMessage(t("actions.defaultMessage"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang]);

  const refreshRecord = (selected = mode) =>
    Promise.all([
      getJson<AgentActionLedger>(`/api/v1/audit-log?mode=${selected}&limit=50`),
      getJson<AuditIntegrityStatus>(`/api/v1/audit-proof/status?mode=${selected}`),
    ]).then(([nextLedger, nextIntegrity]) => {
      setLedger(nextLedger);
      setIntegrity(nextIntegrity);
    });

  useEffect(() => {
    if (mode !== "demo") return;
    const runId = new URLSearchParams(window.location.search).get("runId");
    if (!runId) return;
    setBusy(true);
    setStatus(t("actions.loadingRun"));
    getJson<WorkflowRun>(`/api/v1/workflows/${encodeURIComponent(runId)}`)
      .then((result) => {
        setRun(result);
        setMessage(result.narrative.founder_message);
        setEdited(true);
        setStatus("");
      })
      .catch(() => setStatus(""))
      .finally(() => setBusy(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setLedger(null);
    setIntegrity(null);
    refreshRecord(mode).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  async function analyze() {
    setBusy(true);
    setStatus("");
    try {
      const result = await requestJson<WorkflowRun>("/api/v1/workflows/demo", {
        method: "POST",
        body: JSON.stringify({ founder_message: message }),
      });
      setRun(result);
      await refreshRecord("demo");
    } catch {
      setStatus(t("actions.startError"));
    } finally {
      setBusy(false);
    }
  }

  async function decide(outcome: "approve" | "reject") {
    if (!run) return;
    setBusy(true);
    setStatus("");
    try {
      const result = await requestJson<WorkflowRun>(`/api/v1/workflows/${run.run_id}/decision`, {
        method: "POST",
        body: JSON.stringify({
          outcome,
          actor: "founder-ui",
          reason: outcome === "approve" ? "Approved in Founder UI" : "Rejected in Founder UI",
          action_digest: run.approval.action_digest,
        }),
      });
      setRun(result);
      await refreshRecord("demo");
    } catch {
      setStatus(t("actions.decideError"));
    } finally {
      setBusy(false);
    }
  }

  if (mode === "live") {
    return (
      <main className="page">
        <h1>{t("actions.title")}</h1>
        <p className="lead">{t("actions.live.title")}</p>
        <p className="muted">{t("actions.live.body")}</p>
        {ledger && integrity && <ActionLedger ledger={ledger} integrity={integrity} />}
      </main>
    );
  }

  const problem = run?.narrative.top_problems[0];
  const impact = run?.narrative.expected_impact;
  const money = (value: string, currency: string) =>
    new Intl.NumberFormat(locale, { style: "currency", currency, maximumFractionDigits: 0 }).format(asNumber(value));

  return (
    <main className="page">
      <h1>{t("actions.title")}</h1>
      <p className="lead">{t("actions.subtitle")}</p>

      <form
        className="ask"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && message.trim()) analyze();
        }}
      >
        <label htmlFor="founder-message">{t("actions.prompt")}</label>
        <textarea
          id="founder-message"
          maxLength={500}
          onChange={(event) => {
            setMessage(event.target.value);
            setEdited(true);
          }}
          rows={3}
          value={message}
        />
        <div className="actions-row">
          <button className="button" disabled={busy || !message.trim()} type="submit">
            {busy && !run ? t("actions.analyzing") : t("actions.analyze")}
          </button>
          {status && <span className="note" role="status">{status}</span>}
        </div>
      </form>

      {run && problem && impact && (
        <div className="conversation" aria-live="polite">
          <p className="reply">{tx(run.narrative.acknowledgement)}</p>

          <section className="section">
            <p className="eyebrow">{t("actions.issue")} · {problem.platform}</p>
            <h2>{tx(problem.problem)}</h2>
            <p>{tx(problem.reason)}</p>
            <details>
              <summary>{t("common.evidence")}</summary>
              <ul className="plain">
                {problem.evidence.map((item) => <li key={item}>{tx(item)}</li>)}
              </ul>
            </details>
          </section>

          <section className="proposal">
            <p className="eyebrow">{t("actions.proposal")}</p>
            <h2>{tx(run.narrative.recommended_actions[0])}</h2>
            <dl className="facts">
              <div><dt>{t("actions.budget")}</dt><dd>{money(impact.budget, impact.currency)}</dd></div>
              <div><dt>{t("actions.roas")}</dt><dd>{impact.expected_roas}x</dd></div>
              <div><dt>{t("actions.window")}</dt><dd>{t("actions.days", { n: impact.measurement_window_days })}</dd></div>
            </dl>
            <details>
              <summary>{t("actions.campaignDetails")}</summary>
              <dl className="facts stacked">
                <div><dt>{t("actions.audience")}</dt><dd>{run.campaign_draft.audience}</dd></div>
                <div><dt>{t("actions.keywords")}</dt><dd>{run.campaign_draft.keywords.join(" · ")}</dd></div>
                <div>
                  <dt>{t("actions.ads")}</dt>
                  <dd>{run.campaign_draft.headlines.join(" · ")}<br />{run.campaign_draft.descriptions.join(" ")}</dd>
                </div>
              </dl>
            </details>
            <p className="note">{tx(impact.disclaimer)}</p>
            {run.approval.status === "pending" && (
              <div className="actions-row">
                <button className="button" disabled={busy} onClick={() => decide("approve")} type="button">
                  {busy ? t("actions.working") : t("actions.approve")}
                </button>
                <button className="link" disabled={busy} onClick={() => decide("reject")} type="button">
                  {t("actions.reject")}
                </button>
              </div>
            )}
            {run.approval.status === "executed" && (
              <p className="outcome ok">{t("actions.executed")} <code>{run.execution?.campaign_resource_name}</code></p>
            )}
            {run.approval.status === "rejected" && <p className="outcome">{t("actions.rejected")}</p>}
          </section>

          <details className="section aside">
            <summary>
              {t("actions.visibility", { score: Math.round(run.geo_analysis.visibility_score) })} ·{" "}
              {tx(run.geo_analysis.brand_status)}
            </summary>
            <ol className="plain numbered">
              {run.geo_analysis.action_plan.map((item) => (
                <li key={item.priority}>
                  <strong>{tx(item.action)}</strong>
                  <small>{tx(item.rationale)}</small>
                </li>
              ))}
            </ol>
          </details>
        </div>
      )}

      {ledger && integrity && <ActionLedger ledger={ledger} integrity={integrity} />}
    </main>
  );
}
