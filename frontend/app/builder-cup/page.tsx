"use client";

import { useMemo, useState } from "react";

import { requestJson, type CompetitionDecision, type CompetitionEvidence } from "../../lib/api";
import { type Language, useI18n } from "../../lib/i18n";

const COPY: Record<Language, Record<string, string>> = {
  en: {
    title: "Evidence-to-action decision lab",
    lead: "Sample SaaS evidence + live Vertex AI reasoning + human-approved simulated execution.",
    disclosure: "This public experience uses synthetic sample business data. It cannot access personal accounts or write to an advertising platform.",
    analyze: "Analyze with GrowthOS",
    analyzing: "Analyzing…",
    priority: "Priority problem",
    evidence: "Cited evidence",
    alternatives: "Two action alternatives",
    observe: "Observe",
    approve: "Approve simulated action",
    reject: "Reject",
    working: "Recording decision…",
    missing: "Missing information",
    live_vertex: "Live Vertex AI",
    cache: "Cached Vertex result",
    deterministic_fallback: "Clearly labeled fallback",
    pending: "Awaiting human approval",
    executed: "Simulated execution recorded",
    rejected: "Action rejected — nothing executed",
    failed: "Execution needs review",
    error: "The request failed. No success was recorded; retry when the service is available.",
  },
  "zh-Hans": {
    title: "从业务证据到行动的决策实验室",
    lead: "SaaS 样例证据 + Vertex AI 实时推理 + 人工审批的模拟执行。",
    disclosure: "此公开体验只使用合成样例业务数据，无法读取个人真实账户，也不会写入广告平台。",
    analyze: "使用 GrowthOS 分析",
    analyzing: "正在分析…",
    priority: "优先问题",
    evidence: "引用证据",
    alternatives: "两项备选行动",
    observe: "观察指标",
    approve: "批准模拟行动",
    reject: "拒绝",
    working: "正在记录决定…",
    missing: "缺失信息",
    live_vertex: "Vertex AI 实时分析",
    cache: "Vertex 缓存结果",
    deterministic_fallback: "明确标记的降级结果",
    pending: "等待人工审批",
    executed: "已记录模拟执行",
    rejected: "已拒绝，没有执行任何操作",
    failed: "执行结果需要核对",
    error: "请求失败，系统没有记录虚假成功；请在服务恢复后重试。",
  },
  "zh-Hant": {
    title: "從業務證據到行動的決策實驗室",
    lead: "SaaS 樣例證據 + Vertex AI 即時推理 + 人工審批的模擬執行。",
    disclosure: "此公開體驗只使用合成樣例業務資料，無法讀取個人真實帳戶，也不會寫入廣告平台。",
    analyze: "使用 GrowthOS 分析",
    analyzing: "正在分析…",
    priority: "優先問題",
    evidence: "引用證據",
    alternatives: "兩項備選行動",
    observe: "觀察指標",
    approve: "批准模擬行動",
    reject: "拒絕",
    working: "正在記錄決定…",
    missing: "缺失資訊",
    live_vertex: "Vertex AI 即時分析",
    cache: "Vertex 快取結果",
    deterministic_fallback: "明確標記的降級結果",
    pending: "等待人工審批",
    executed: "已記錄模擬執行",
    rejected: "已拒絕，沒有執行任何操作",
    failed: "執行結果需要核對",
    error: "請求失敗，系統沒有記錄虛假成功；請在服務恢復後重試。",
  },
};

const SAMPLE_EVIDENCE: CompetitionEvidence[] = [
  { evidence_id: "EV-MRR", label: "Monthly recurring revenue", value: "$30,000, +2% vs prior month", source: "Synthetic Stripe snapshot", observed_at: "2026-10-15T08:00:00Z" },
  { evidence_id: "EV-CAC", label: "Customer acquisition cost", value: "$120, +24% vs prior period", source: "Synthetic Google Ads snapshot", observed_at: "2026-10-15T08:00:00Z" },
  { evidence_id: "EV-ROAS", label: "Paid search ROAS", value: "2.8x vs 3.0x planning target", source: "Synthetic Google Ads snapshot", observed_at: "2026-10-15T08:00:00Z" },
  { evidence_id: "EV-VIS", label: "AI search visibility", value: "31/100 across tracked prompts", source: "Synthetic visibility sample", observed_at: "2026-10-15T08:00:00Z" },
  { evidence_id: "EV-FEEDBACK", label: "Customer feedback", value: "Onboarding setup is the leading negative topic", source: "Synthetic Reddit and X sample", observed_at: "2026-10-15T08:00:00Z" },
];

export default function BuilderCupPage() {
  const { lang } = useI18n();
  const c = COPY[lang];
  const [decision, setDecision] = useState<CompetitionDecision | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const evidenceById = useMemo(() => new Map(SAMPLE_EVIDENCE.map((item) => [item.evidence_id, item])), []);

  async function analyze() {
    setBusy(true);
    setError("");
    try {
      setDecision(await requestJson<CompetitionDecision>("/api/v1/competition/analyze", {
        method: "POST",
        body: JSON.stringify({
          founder_goal: "Choose the highest-leverage action for the next seven days without inventing facts.",
          evidence: SAMPLE_EVIDENCE,
        }),
      }));
    } catch {
      setError(c.error);
    } finally {
      setBusy(false);
    }
  }

  async function decide(outcome: "approve" | "reject") {
    if (!decision) return;
    setBusy(true);
    setError("");
    try {
      setDecision(await requestJson<CompetitionDecision>(
        `/api/v1/competition/workflows/${decision.workflow_id}/decision`,
        {
          method: "POST",
          body: JSON.stringify({ outcome, action_digest: decision.action_digest, reason: "Reviewed in the public Builder Cup sandbox." }),
        },
      ));
    } catch {
      setError(c.error);
    } finally {
      setBusy(false);
    }
  }

  const showEvidence = (ids: string[]) => ids.map((id) => evidenceById.get(id)).filter(Boolean) as CompetitionEvidence[];

  return (
    <main className="page">
      <p className="eyebrow">AI Builder Cup 2026 · Future of Work &amp; Enterprise Productivity</p>
      <h1>{c.title}</h1>
      <p className="lead">{c.lead}</p>
      <p className="note">{c.disclosure}</p>

      <section className="section">
        <p className="eyebrow">Sample data · {SAMPLE_EVIDENCE.length} facts</p>
        <dl className="facts stacked">
          {SAMPLE_EVIDENCE.map((item) => <div key={item.evidence_id}><dt>{item.evidence_id} · {item.label}</dt><dd>{item.value}<small>{item.source}</small></dd></div>)}
        </dl>
        <button className="button" disabled={busy} onClick={analyze} type="button">{busy && !decision ? c.analyzing : c.analyze}</button>
        {error && <p className="outcome" role="alert">{error}</p>}
      </section>

      {decision && (
        <div className="conversation" aria-live="polite">
          <section className="focus">
            <p className="eyebrow">{c.priority} · {c[decision.source]}</p>
            <h2>{decision.priority_problem}</h2>
            <p>{decision.priority_reason}</p>
            <p className="note">{decision.model_id ?? "No live model"} · {new Date(decision.generated_at).toLocaleString()}</p>
            <details><summary>{c.evidence}</summary><ul className="plain">{showEvidence(decision.priority_evidence_ids).map((item) => <li key={item.evidence_id}><strong>{item.evidence_id}</strong> {item.label}: {item.value}</li>)}</ul></details>
          </section>

          <p className="eyebrow">{c.alternatives}</p>
          {decision.alternatives.map((action, index) => (
            <section className={index === 0 ? "proposal" : "section"} key={action.title}>
              <p className="eyebrow">{index + 1} · {action.execution_kind === "simulated_campaign" ? "Approval-gated simulation" : "Manual recommendation"}</p>
              <h2>{action.title}</h2>
              <p>{action.rationale}</p>
              <p><strong>{c.observe}:</strong> {action.observation_metric}</p>
              <details><summary>{c.evidence}</summary><ul className="plain">{showEvidence(action.evidence_ids).map((item) => <li key={item.evidence_id}><strong>{item.evidence_id}</strong> {item.value}</li>)}</ul></details>
              {index === 0 && decision.approval_status === "pending" && <div className="actions-row"><button className="button" disabled={busy} onClick={() => decide("approve")} type="button">{busy ? c.working : c.approve}</button><button className="link" disabled={busy} onClick={() => decide("reject")} type="button">{c.reject}</button></div>}
              {index === 0 && decision.approval_status !== "pending" && <p className="outcome ok">{c[decision.approval_status] ?? decision.approval_status}</p>}
            </section>
          ))}

          {decision.missing_information.length > 0 && <section className="section aside"><p className="eyebrow">{c.missing}</p><ul className="plain">{decision.missing_information.map((item) => <li key={item}>{item}</li>)}</ul></section>}
          {decision.failure_message && <p className="outcome">{decision.failure_message}</p>}
        </div>
      )}
    </main>
  );
}
