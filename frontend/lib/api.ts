import { firebaseIdentityKey, getFirebaseIdToken } from "./firebase";

// Public hosting uses same-origin /api rewrites. Local development can opt into an explicit URL.
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
export const AUTH_SESSION_INVALID_EVENT = "growthos-session-invalid";

export type NumberValue = number | string;

export type DataMode = "demo" | "live";

export type WorkspaceSession = {
  uid: string | null;
  workspace_id: string | null;
  authenticated: boolean;
  live_allowed: boolean;
};

export function isWorkspaceSession(value: unknown): value is WorkspaceSession {
  if (!value || typeof value !== "object") return false;
  const session = value as Record<string, unknown>;
  if ((session.uid !== null && (typeof session.uid !== "string" || !session.uid.trim())) ||
      typeof session.authenticated !== "boolean" || typeof session.live_allowed !== "boolean") return false;
  const hasWorkspace = typeof session.workspace_id === "string" && session.workspace_id.trim().length > 0;
  if (session.live_allowed) return session.authenticated && hasWorkspace;
  return session.workspace_id === null || hasWorkspace;
}

export type CompetitionEvidence = {
  evidence_id: string;
  label: string;
  value: string;
  source: string;
  observed_at: string | null;
};

export type CompetitionAction = {
  title: string;
  rationale: string;
  evidence_ids: string[];
  observation_metric: string;
  execution_kind: "simulated_campaign" | "manual_recommendation";
};

export type CompetitionDecision = {
  language: "en" | "zh-Hans" | "zh-Hant";
  workflow_id: string;
  priority_problem: string;
  priority_reason: string;
  priority_evidence_ids: string[];
  alternatives: [CompetitionAction, CompetitionAction];
  missing_information: string[];
  evidence: CompetitionEvidence[];
  source: "live_vertex" | "cache" | "deterministic_fallback";
  model_id: string | null;
  cached: boolean;
  generated_at: string;
  approval_status: "pending" | "executing" | "executed" | "rejected" | "failed";
  action_digest: string;
  execution_result: { kind: string; status: string; message: string } | null;
  failure_message: string | null;
};

export type DataProvenance = {
  mode: DataMode;
  label: string;
  detail: string;
};

export const LEGACY_DEMO_PROVENANCE: DataProvenance = {
  mode: "demo",
  label: "Legacy demo payload",
  detail: "Synthetic demo data from an older local API process. Restart the API to load per-item source metadata.",
};

export function displayProvenance(source?: DataProvenance): DataProvenance {
  return source ?? LEGACY_DEMO_PROVENANCE;
}

export type BriefingSignal = {
  key: string;
  label: string;
  current_value: string;
  change_pct: NumberValue | null;
  context: string;
  provenance: DataProvenance;
};

export type FounderDailyBriefing = {
  salutation: string;
  company_name: string;
  growth_health: NumberValue;
  health_band: string;
  executive_summary: string;
  yesterday: BriefingSignal[];
  main_risk: string;
  risk_detail: string;
  top_recommendation: string;
  recommendation_reason: string;
  provenance: DataProvenance;
  pending_approval_id: string | null;
  generated_at: string;
};

export type DashboardMetric = {
  key: string;
  label: string;
  value: NumberValue | null;
  formatted_value: string;
  trend: string;
  source: string;
  provenance: DataProvenance;
  tone: "positive" | "watch" | "attention" | "neutral";
};

export type DashboardRecommendation = {
  priority: number;
  source_agent: string;
  action: string;
  rationale: string;
  provenance: DataProvenance;
  workflow_run_id: string | null;
  approval_id: string | null;
  approval_status: string | null;
};

export type ClassifiedFeedback = {
  source: "reddit" | "x";
  text: string;
  sentiment: "positive" | "mixed" | "neutral" | "negative";
  topics: string[];
};

export type CustomerProblem = {
  topic: string;
  problem: string;
  mentions: number;
  impact: string;
  evidence: string[];
};

export type FeedbackAnalysis = {
  sample_count: number;
  source_counts: Record<"reddit" | "x", number>;
  overall_sentiment: "positive" | "mixed" | "neutral" | "negative";
  sentiment_score: NumberValue;
  positive_pct: NumberValue;
  negative_pct: NumberValue;
  main_problem: string;
  problems: CustomerProblem[];
  recommendation: string;
  analyzed_signals: ClassifiedFeedback[];
  generated_at: string;
};

export type GrowthDashboard = {
  company_name: string;
  metrics: DashboardMetric[];
  recommendations: DashboardRecommendation[];
  customer_intelligence: FeedbackAnalysis;
  customer_intelligence_provenance: DataProvenance;
  provenance: DataProvenance;
  generated_at: string;
};

export type FounderExperience = {
  briefing: FounderDailyBriefing;
  dashboard: GrowthDashboard;
};

export type DemoStageId =
  | "problem"
  | "connect_data"
  | "diagnosis"
  | "execution"
  | "future";

export type DemoScript = {
  script_id: string;
  fixed_case: true;
  total_duration_seconds: 180;
  case: {
    title: string;
    company_name: string;
    team_size: number;
    mrr: NumberValue;
    currency: string;
    growth_problems: [string, string];
    founder_context: string;
  };
  data_sources: Array<{
    provider: "stripe" | "google_ads" | "meta_ads";
    label: string;
    signal: string;
    status: "connected";
  }>;
  diagnosis: {
    growth_score: NumberValue;
    score_band: string;
    problems: Array<{
      title: string;
      evidence: string;
      source: string;
      severity: "high" | "medium";
    }>;
  };
  actions: Array<{
    kind: "create_campaign" | "geo_plan";
    title: string;
    agent: string;
    output: string;
    status: "ready_for_approval" | "plan_ready";
    guardrail: string;
  }>;
  future: Array<{
    title: "Growth Passport" | "Growth Capital";
    description: string;
    availability: "future";
  }>;
  stages: Array<{
    id: DemoStageId;
    label: string;
    title: string;
    start_seconds: number;
    end_seconds: number;
    objective: string;
    presenter_cue: string;
    proof_points: string[];
  }>;
};

export type AccountEnvironment = "test" | "live" | "unknown";

export type SourceReportingMetadata = {
  account_environment?: AccountEnvironment;
  period?: { start: string; end: string };
  reporting_timezone?: string | null;
  collected_at?: string | null;
};

export type LiveGrowthIntelligence = {
  audit_recorded?: boolean;
  period_policy?: "common_complete_calendar_days" | "fixture";
  data_source_mode: "live";
  company: {
    company_name: string;
  } | null;
  revenue: SourceReportingMetadata & {
    revenue: NumberValue;
    mrr: NumberValue | null;
    mrr_status?: "complete" | "incomplete";
    mrr_policy?: string;
    mrr_warnings?: string[];
    mrr_as_of?: string | null;
    currency: string;
    growth_rate_pct: NumberValue | null;
  };
  google_ads: SourceReportingMetadata & {
    roas: NumberValue | null;
    cac: NumberValue | null;
    spend: NumberValue;
    currency: string;
  };
  meta_ads: SourceReportingMetadata & {
    roas: NumberValue | null;
    cac: NumberValue | null;
    spend: NumberValue;
    currency: string;
  };
  growth_score: {
    status?: "available" | "insufficient_data";
    score: NumberValue | null;
    band: string | null;
    missing_components?: string[];
  };
  generated_at: string;
};

export type AgentActionStatus =
  | "awaiting_approval"
  | "executed"
  | "rejected"
  | "failed";

export type AgentActionLedgerEntry = {
  entry_id: string;
  mode: DataMode;
  agent: string;
  action: string;
  reason: string;
  timestamp: string;
  status: AgentActionStatus;
  workflow_run_id: string | null;
  approval_id: string | null;
};

export type AgentActionLedger = {
  mode: DataMode;
  entries: AgentActionLedgerEntry[];
  total: number;
};

export type AuditIntegrityStatus = {
  mode: DataMode;
  state: "verified" | "not_anchored" | "empty" | "mismatch";
  record_count: number;
  pending_record_count: number;
  batch_id: string | null;
  root_hash: string | null;
  network: string | null;
  chain_id: number | null;
  contract_address: string | null;
  transaction_hash: string | null;
  block_number: number | null;
  explorer_url: string | null;
  verified_at: string | null;
};

export type ConnectorProvider =
  | "openai"
  | "gemini"
  | "gemini_vertex"
  | "perplexity"
  | "x"
  | "reddit"
  | "google_ads"
  | "meta_ads"
  | "stripe";

export type ConnectionAccount = {
  account_id: string;
  label: string;
};

export type ConnectionStatus = {
  account_environment?: AccountEnvironment;
  currency?: string | null;
  saved_at?: string | null;
  expires_at?: string | null;
  storage_status?: "saved" | "missing" | "expired" | "unavailable" | "server_environment" | "unknown";
  provider: ConnectorProvider;
  display_name: string;
  category: string;
  state: "connected" | "needs_configuration" | "fallback_ready";
  configured: boolean;
  auth_method: string;
  configuration_source: string | null;
  project_id: string | null;
  location: string | null;
  api_version: string | null;
  selected_account_id: string | null;
  accounts: ConnectionAccount[];
  supports_live_test: boolean;
  demo_available: boolean;
  setup_kind: "api_key" | "cloud_project" | "automatic" | "oauth" | "restricted_key";
  setup_label: string;
  setup_steps: string[];
  estimated_setup_minutes: number;
  note: string;
};

export type ConnectionCenter = {
  tenant_id: string;
  vault_encryption_enabled: boolean;
  connections: ConnectionStatus[];
};

export type ConnectionTestResult = {
  account_environment?: AccountEnvironment;
  checked_at?: string | null;
  provider: ConnectorProvider;
  configured: boolean;
  checked_live: boolean;
  success: boolean;
  message: string;
  latency_ms: number | null;
};

export type GeoCitation = {
  title: string;
  url: string;
};

export type GeoPromptResult = {
  provider: ConnectorProvider;
  prompt: string;
  answer: string;
  brand_mentioned: boolean;
  mention_rank: number | null;
  brand_cited: boolean;
  citations: GeoCitation[];
  latency_ms: number;
  error: string | null;
};

export type ProviderVisibility = {
  provider: ConnectorProvider;
  attempted_prompts: number;
  successful_prompts: number;
  brand_mentions: number;
  branded_citations: number;
  visibility_score: NumberValue;
  status: string;
};

export type GeoScanReport = {
  tenant_id: string;
  mode: DataMode;
  brand: string;
  website: string;
  overall_visibility_score: NumberValue;
  providers: ProviderVisibility[];
  results: GeoPromptResult[];
  provenance: string;
  generated_at: string;
};

export type FeedbackSourceResult = {
  provider: ConnectorProvider;
  status: string;
  signal_count: number;
  collection_method: string;
  error: string | null;
};

export type FeedbackCollectionResponse = {
  tenant_id: string;
  mode: DataMode;
  brand: string;
  sources: FeedbackSourceResult[];
  signals: Array<{ source: "reddit" | "x"; text: string; external_id: string | null }>;
  analysis: FeedbackAnalysis | null;
  generated_at: string;
};

export type IntelligenceKind = "brand_visibility" | "customer_intelligence";

export type IntelligenceSnapshot = {
  snapshot_id: string;
  tenant_id: string;
  mode: DataMode;
  kind: IntelligenceKind;
  brand: string;
  score: NumberValue | null;
  metrics: Record<string, NumberValue>;
  summary: string;
  result: GeoScanReport | FeedbackCollectionResponse;
  related_action_ids: string[];
  generated_at: string;
  saved_at: string;
};

export type ObservedActionEffect = {
  entry_id: string;
  agent: string;
  action: string;
  timestamp: string;
  observed_score_delta: NumberValue;
  measurement_kind: IntelligenceKind;
  current_snapshot_id: string;
};

export type IntelligenceHistoryItem = {
  snapshot: IntelligenceSnapshot;
  previous_snapshot_id: string | null;
  score_delta: NumberValue | null;
  metric_deltas: Record<string, NumberValue>;
  trend: "baseline" | "improved" | "declined" | "unchanged";
  observed_after_actions: ObservedActionEffect[];
};

export type IntelligenceHistory = {
  mode: DataMode;
  kind: IntelligenceKind | null;
  items: IntelligenceHistoryItem[];
  total: number;
  top_observed_effects: ObservedActionEffect[];
};

export async function getJson<T>(path: string): Promise<T> {
  return requestJson<T>(path);
}

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const bodyMode = typeof init.body === "string" ? (() => { try { return JSON.parse(init.body).mode; } catch { return null; } })() : null;
  const registered = path.startsWith("/api/v1/connections") || path.startsWith("/api/v1/growth-intelligence") || /[?&]mode=live(?:&|$)/.test(path) || bodyMode === "live";
  const initialIdentity = firebaseIdentityKey();
  const invalidateSession = () => {
    if (registered && firebaseIdentityKey() === initialIdentity && typeof window !== "undefined") {
      window.dispatchEvent(new Event(AUTH_SESSION_INVALID_EVENT));
    }
  };
  const tokenForRequest = async (force: boolean) => {
    try { return await getFirebaseIdToken(force, registered); }
    catch (error) {
      const code = typeof error === "object" && error !== null && "code" in error ? String(error.code) : error instanceof Error ? error.message : "";
      if (["auth/registered-user-required", "auth/user-token-expired", "auth/invalid-user-token", "auth/user-disabled", "auth/user-not-found"].includes(code)) {
        invalidateSession();
        throw new ApiError("auth/session-expired", 401);
      }
      throw error;
    }
  };
  const identityToken = await tokenForRequest(false);
  const identity = firebaseIdentityKey();
  const ensureIdentity = () => {
    if ((initialIdentity && initialIdentity !== identity) || firebaseIdentityKey() !== identity) {
      throw new ApiError("auth/identity-changing", 401);
    }
  };
  const send = (token: string | null) => {
    ensureIdentity();
    return fetch(`${API_BASE}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...init.headers,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
  };
  let response = await send(identityToken);
  // A server-rejected identity never reaches the action handler, so a single
  // refreshed-token retry is safe even for approval requests.
  ensureIdentity();
  if (response.status === 401 && identityToken) response = await send(await tokenForRequest(true));
  ensureIdentity();
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    ensureIdentity();
    if (response.status === 401 || response.status === 403) invalidateSession();
    const detail = payload?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item) => typeof item?.msg === "string" ? item.msg : "Invalid input").join("; ")
      : `Request failed with ${response.status}`;
    throw new ApiError(message, response.status);
  }
  const payload = (await response.json()) as T;
  ensureIdentity();
  return payload;
}

export function asNumber(value: NumberValue): number {
  return typeof value === "number" ? value : Number(value);
}

export function signedPercent(value: NumberValue): string {
  const number = asNumber(value);
  return `${number >= 0 ? "+" : ""}${number.toFixed(0)}%`;
}
