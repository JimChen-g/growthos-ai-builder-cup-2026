"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useDataMode } from "../../components/DataModeContext";
import { ApiError, requestJson, type CompetitionDecision, type CompetitionEvidence } from "../../lib/api";
import { type Language, useI18n } from "../../lib/i18n";

const COPY: Record<Language, Record<string, string>> = {
  en: {
    eyebrow: "AI Builder Cup 2026 · Future of Work & Enterprise Productivity",
    title: "From evidence to action: AI decision demo",
    lead: "Sample SaaS evidence + real Vertex AI reasoning + human-approved simulated execution.",
    disclosure: "A competition demo of the decision workflow, not a live business dashboard. It uses synthetic business data, cannot access personal accounts and never writes to advertising platforms.",
    languageHint: "Switching languages generates an analysis in that language. It never automatically approves or executes an action.",
    sample: "Sample data · 5 facts", analyze: "Analyze with GrowthOS", analyzing: "Analyzing…",
    priority: "Priority problem", evidence: "Cited evidence", alternatives: "Two action alternatives", observe: "Observe",
    approve: "Approve simulated action", reject: "Reject", working: "Recording decision…", missing: "Missing information",
    live_vertex: "Real-time Vertex AI analysis", cache: "Cached Vertex AI analysis", deterministic_fallback: "Fallback recommendation · no AI inference",
    pending: "Awaiting human approval", executing: "Recording simulated execution", executed: "Simulated execution recorded",
    rejected: "Action rejected — nothing executed", failed: "Execution needs review", noModel: "No live model",
    simulation: "Approval-gated simulation", manual: "Manual recommendation",
    error: "The request could not be completed. Please retry; no successful execution is being claimed.",
    quota: "The demo's usage limit has been reached. Please try again later.",
    goal: "Choose the highest-leverage action for the next seven days without inventing facts.",
    reason: "Reviewed in the public Builder Cup sandbox.",
    sampleOnly: "This decision demo uses sample data only.",
    liveHint: "It is separate from your connected accounts. Switch to Sample to explore the demonstration.",
    switchSample: "Open sample demo",
  },
  "zh-Hans": {
    eyebrow: "AI Builder Cup 2026 · 未来工作与企业生产力",
    title: "从业务证据到行动：AI 决策演示",
    lead: "SaaS 示例证据 + Vertex AI 真实推理 + 人工审批的模拟执行。",
    disclosure: "这是为比赛展示决策流程的演示，不是真实业务看板。仅使用合成业务数据，无法读取个人账户，也不会向广告平台写入任何操作。",
    languageHint: "切换语言后，会生成对应语言的分析；不会自动批准或执行行动。",
    sample: "示例数据 · 5 项事实", analyze: "使用 GrowthOS 分析", analyzing: "正在分析…",
    priority: "优先问题", evidence: "引用证据", alternatives: "两项备选行动", observe: "观察指标",
    approve: "批准模拟行动", reject: "拒绝", working: "正在记录决定…", missing: "缺失信息",
    live_vertex: "Vertex AI 实时分析", cache: "Vertex AI 缓存分析", deterministic_fallback: "备用建议 · 未使用 AI 推理",
    pending: "等待人工审批", executing: "正在记录模拟执行", executed: "已记录模拟执行",
    rejected: "已拒绝，没有执行任何操作", failed: "执行结果需要核对", noModel: "未调用实时模型",
    simulation: "需审批的模拟行动", manual: "人工执行建议",
    error: "请求未能完成，请重试；当前不会将其标记为执行成功。",
    quota: "演示使用额度已用完，请稍后再试。",
    goal: "在不编造事实的前提下，选择未来七天最值得优先采取的行动。",
    reason: "已在 Builder Cup 公开演示环境中审阅。",
    sampleOnly: "AI 决策演示仅使用示例数据。", liveHint: "此演示与已连接的真实账户相互独立，请切换到示例工作区体验。", switchSample: "打开示例演示",
  },
  "zh-Hant": {
    eyebrow: "AI Builder Cup 2026 · 未來工作與企業生產力",
    title: "從業務證據到行動：AI 決策示範",
    lead: "SaaS 範例證據 + Vertex AI 真實推理 + 人工審批的模擬執行。",
    disclosure: "這是為比賽展示決策流程的示範，不是真實業務看板。僅使用合成業務資料，無法讀取個人帳戶，也不會向廣告平台寫入任何操作。",
    languageHint: "切換語言後，會產生對應語言的分析；不會自動批准或執行行動。",
    sample: "範例資料 · 5 項事實", analyze: "使用 GrowthOS 分析", analyzing: "正在分析…",
    priority: "優先問題", evidence: "引用證據", alternatives: "兩項備選行動", observe: "觀察指標",
    approve: "批准模擬行動", reject: "拒絕", working: "正在記錄決定…", missing: "缺失資訊",
    live_vertex: "Vertex AI 即時分析", cache: "Vertex AI 快取分析", deterministic_fallback: "備用建議 · 未使用 AI 推理",
    pending: "等待人工審批", executing: "正在記錄模擬執行", executed: "已記錄模擬執行",
    rejected: "已拒絕，沒有執行任何操作", failed: "執行結果需要核對", noModel: "未呼叫即時模型",
    simulation: "需審批的模擬行動", manual: "人工執行建議",
    error: "請求未能完成，請重試；目前不會將其標記為執行成功。",
    quota: "示範使用額度已用完，請稍後再試。",
    goal: "在不編造事實的前提下，選擇未來七天最值得優先採取的行動。",
    reason: "已在 Builder Cup 公開示範環境中審閱。",
    sampleOnly: "AI 決策示範僅使用範例資料。", liveHint: "此示範與已連接的真實帳戶相互獨立，請切換到範例工作區體驗。", switchSample: "開啟範例示範",
  },
};

const EVIDENCE_TEXT: Record<Language, string[][]> = {
  en: [
    ["Monthly recurring revenue", "$30,000, +2% vs prior month", "Synthetic Stripe snapshot"],
    ["Customer acquisition cost", "$120, +24% vs prior period", "Synthetic Google Ads snapshot"],
    ["Paid search ROAS", "2.8x vs 3.0x planning target", "Synthetic Google Ads snapshot"],
    ["AI search visibility", "31/100 across tracked prompts", "Synthetic visibility sample"],
    ["Customer feedback", "Onboarding setup is the leading negative topic", "Synthetic Reddit and X sample"],
  ],
  "zh-Hans": [
    ["月度经常性收入", "$30,000，较上月增长 2%", "合成 Stripe 数据快照"],
    ["获客成本", "$120，较上一周期增长 24%", "合成 Google Ads 数据快照"],
    ["付费搜索广告支出回报率（ROAS）", "实际 2.8 倍，计划目标 3.0 倍", "合成 Google Ads 数据快照"],
    ["AI 搜索可见度", "在跟踪的查询中得分 31/100", "合成可见度示例"],
    ["客户反馈", "新用户初始设置是最主要的负面反馈主题", "合成 Reddit 和 X 反馈示例"],
  ],
  "zh-Hant": [
    ["月度經常性收入", "$30,000，較上月成長 2%", "合成 Stripe 資料快照"],
    ["獲客成本", "$120，較上一週期增加 24%", "合成 Google Ads 資料快照"],
    ["付費搜尋廣告支出報酬率（ROAS）", "實際 2.8 倍，計畫目標 3.0 倍", "合成 Google Ads 資料快照"],
    ["AI 搜尋可見度", "在追蹤的查詢中得分 31/100", "合成可見度範例"],
    ["客戶回饋", "新使用者初始設定是最主要的負面回饋主題", "合成 Reddit 和 X 回饋範例"],
  ],
};
const EVIDENCE_IDS = ["EV-MRR", "EV-CAC", "EV-ROAS", "EV-VIS", "EV-FEEDBACK"];
const SAMPLE_EVIDENCE = Object.fromEntries(Object.entries(EVIDENCE_TEXT).map(([language, rows]) => [language,
  rows.map(([label, value, source], index) => ({ evidence_id: EVIDENCE_IDS[index], label, value, source, observed_at: "2026-10-15T08:00:00Z" })),
])) as Record<Language, CompetitionEvidence[]>;

export default function BuilderCupPage() {
  const { lang } = useI18n();
  const { mode, setMode } = useDataMode();
  const c = COPY[lang];
  if (mode === "live") return <main className="page"><h1>{c.sampleOnly}</h1><p className="lead">{c.liveHint}</p><section className="section"><button className="button" onClick={() => setMode("demo")} type="button">{c.switchSample}</button></section></main>;
  return <DecisionDemo />;
}

function DecisionDemo() {
  const { lang } = useI18n();
  const c = COPY[lang];
  const [results, setResults] = useState<Partial<Record<Language, CompetitionDecision>>>({});
  const [busy, setBusy] = useState<Partial<Record<Language, "analyze" | "decision" | null>>>({});
  const [errors, setErrors] = useState<Partial<Record<Language, string>>>({});
  const [started, setStarted] = useState(false);
  const attempted = useRef(new Set<Language>());
  const inFlight = useRef(new Set<Language>());
  const decision = results[lang];
  const evidence = SAMPLE_EVIDENCE[lang];
  const evidenceById = useMemo(() => new Map(evidence.map((item) => [item.evidence_id, item])), [evidence]);

  const analyze = useCallback(async (language: Language) => {
    if (inFlight.current.has(language)) return;
    inFlight.current.add(language);
    attempted.current.add(language);
    setStarted(true);
    setBusy((current) => ({ ...current, [language]: "analyze" }));
    setErrors((current) => ({ ...current, [language]: "" }));
    try {
      const result = await requestJson<CompetitionDecision>("/api/v1/competition/analyze", {
        method: "POST", body: JSON.stringify({ language, founder_goal: COPY[language].goal, evidence: SAMPLE_EVIDENCE[language] }),
      });
      if (result.language !== language) throw new Error("Analysis language mismatch");
      setResults((current) => ({ ...current, [language]: result }));
    } catch (error) {
      setErrors((current) => ({ ...current, [language]: COPY[language][error instanceof ApiError && error.status === 429 ? "quota" : "error"] }));
    } finally {
      inFlight.current.delete(language);
      setBusy((current) => ({ ...current, [language]: null }));
    }
  }, []);

  // Separate responses and approvals by locale: late results cannot replace the
  // selected language, and language changes can never approve an action.
  useEffect(() => {
    if (started && !attempted.current.has(lang)) void analyze(lang);
  }, [lang, started, analyze]);

  async function decide(outcome: "approve" | "reject") {
    if (!decision || inFlight.current.has(lang)) return;
    const language = lang;
    inFlight.current.add(language);
    setBusy((current) => ({ ...current, [language]: "decision" }));
    setErrors((current) => ({ ...current, [language]: "" }));
    try {
      const result = await requestJson<CompetitionDecision>(`/api/v1/competition/workflows/${decision.workflow_id}/decision`, {
        method: "POST", body: JSON.stringify({ outcome, action_digest: decision.action_digest, reason: c.reason }),
      });
      if (result.language !== language) throw new Error("Analysis language mismatch");
      setResults((current) => ({ ...current, [language]: result }));
    } catch {
      setErrors((current) => ({ ...current, [language]: COPY[language].error }));
    } finally {
      inFlight.current.delete(language);
      setBusy((current) => ({ ...current, [language]: null }));
    }
  }

  const showEvidence = (ids: string[]) => ids.map((id) => evidenceById.get(id)).filter(Boolean) as CompetitionEvidence[];
  return (
    <main className="page decision-demo">
      <p className="eyebrow">{c.eyebrow}</p><h1>{c.title}</h1>
      <p className="lead">{c.lead}</p><p className="note">{c.disclosure}</p>
      <section className="section">
        <p className="eyebrow">{c.sample}</p>
        <dl className="facts stacked">{evidence.map((item) => <div key={item.evidence_id}><dt>{item.evidence_id} · {item.label}</dt><dd>{item.value}<small>{item.source}</small></dd></div>)}</dl>
        <button className="button" disabled={Boolean(busy[lang])} onClick={() => void analyze(lang)} type="button">{busy[lang] === "analyze" ? c.analyzing : c.analyze}</button>
        <p className="note language-hint">{c.languageHint}</p>
        {errors[lang] && <p className="outcome" role="alert">{errors[lang]}</p>}
        {busy[lang] === "analyze" && !decision && <p className="note" role="status">{c.analyzing}</p>}
      </section>
      {decision && <div className="conversation" aria-live="polite">
        <section className="focus">
          <p className="eyebrow">{c.priority} · {c[decision.source]}</p>
          <h2>{decision.priority_problem}</h2><p>{decision.priority_reason}</p>
          <p className="note">{decision.model_id ?? c.noModel} · {new Date(decision.generated_at).toLocaleString(lang)}</p>
          <details><summary>{c.evidence}</summary><ul className="plain">{showEvidence(decision.priority_evidence_ids).map((item) => <li key={item.evidence_id}><strong>{item.evidence_id}</strong> {item.label}: {item.value}</li>)}</ul></details>
        </section>
        <p className="eyebrow">{c.alternatives}</p>
        {decision.alternatives.map((action, index) => <section className={index === 0 ? "proposal" : "section"} key={action.title}>
          <p className="eyebrow">{index + 1} · {action.execution_kind === "simulated_campaign" ? c.simulation : c.manual}</p>
          <h2>{action.title}</h2><p>{action.rationale}</p><p><strong>{c.observe}:</strong> {action.observation_metric}</p>
          <details><summary>{c.evidence}</summary><ul className="plain">{showEvidence(action.evidence_ids).map((item) => <li key={item.evidence_id}><strong>{item.evidence_id}</strong> {item.value}</li>)}</ul></details>
          {index === 0 && decision.approval_status === "pending" && <><p className="note">{c.pending}</p><div className="actions-row"><button className="button" disabled={Boolean(busy[lang])} onClick={() => void decide("approve")} type="button">{busy[lang] === "decision" ? c.working : c.approve}</button><button className="link" disabled={Boolean(busy[lang])} onClick={() => void decide("reject")} type="button">{c.reject}</button></div></>}
          {index === 0 && decision.approval_status !== "pending" && <p className={`outcome ${decision.approval_status === "executed" ? "ok" : ""}`}>{c[decision.approval_status]}</p>}
          {index === 0 && decision.execution_result && <p className="note">{decision.execution_result.message}</p>}
        </section>)}
        {decision.missing_information.length > 0 && <section className="section aside"><p className="eyebrow">{c.missing}</p><ul className="plain">{decision.missing_information.map((item) => <li key={item}>{item}</li>)}</ul></section>}
        {decision.failure_message && <p className="outcome" role="alert">{decision.failure_message}</p>}
      </div>}
    </main>
  );
}
