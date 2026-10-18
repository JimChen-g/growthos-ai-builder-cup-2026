"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useDataMode } from "../../components/DataModeContext";
import { PageState } from "../../components/PageState";
import { Segmented } from "../../components/Segmented";
import {
  ApiError,
  getJson,
  requestJson,
  type ConnectionCenter,
  type ConnectionStatus,
  type ConnectionTestResult,
  type ConnectorProvider,
} from "../../lib/api";
import { type MessageKey, useI18n } from "../../lib/i18n";
import { formatTimestamp, maskedWorkspace } from "../../lib/reporting";

const GROUPS: Array<{ title: MessageKey; hint: MessageKey; providers: ConnectorProvider[] }> = [
  { title: "connections.business", hint: "connections.businessHint", providers: ["stripe", "google_ads", "meta_ads"] },
  { title: "connections.ai", hint: "connections.aiHint", providers: ["openai", "gemini", "gemini_vertex", "perplexity"] },
  { title: "connections.voice", hint: "connections.voiceHint", providers: ["x", "reddit"] },
];
const KEY_PROVIDERS: ConnectorProvider[] = ["stripe", "openai", "gemini", "perplexity", "x"];
const BUSINESS_PROVIDERS: ConnectorProvider[] = ["stripe", "google_ads", "meta_ads"];

function connectionError(caught: unknown): MessageKey {
  if (caught instanceof ApiError) {
    if ([401, 403].includes(caught.status)) return "live.authRequired";
    if (caught.message.includes("account currency does not match")) return "connections.currencyMismatch";
    if (caught.message.includes("reporting timezone")) return "growth.live.timezoneUnavailable";
    if (caught.status === 404) return "live.serviceUnavailable";
    if ([400, 422].includes(caught.status)) return "connections.invalid";
    if (caught.status === 409) return "connections.configurationUnavailable";
    if (caught.status === 429) return "live.quota";
  }
  return "live.requestError";
}

export default function ConnectionsPage() {
  const { mode, identity, session } = useDataMode();
  const { t } = useI18n();
  const [center, setCenter] = useState<ConnectionCenter | null>(null);
  const [failed, setFailed] = useState<MessageKey | null>(null);

  const refresh = () => getJson<ConnectionCenter>("/api/v1/connections").then(setCenter);

  useEffect(() => {
    if (mode !== "live") return;
    const controller = new AbortController();
    setFailed(null);
    setCenter(null);
    requestJson<ConnectionCenter>("/api/v1/connections", { signal: controller.signal })
      .then(setCenter)
      .catch((caught) => {
        if (!controller.signal.aborted) setFailed(connectionError(caught));
      });
    return () => controller.abort();
  }, [mode]);

  if (mode === "demo") {
    return (
      <main className="page">
        <h1>{t("connections.title")}</h1>
        <p className="lead">{t("mode.demoHint")}</p>
        <Link className="button" href="/">{t("nav.today")}</Link>
      </main>
    );
  }
  if (failed) return <main className="page">
    <h1>{t("connections.title")}</h1>
    <p className="note warn" role="alert">{t(failed)}</p>
    <button className="button" type="button" onClick={() => { setFailed(null); refresh().catch((caught) => setFailed(connectionError(caught))); }}>{t("mode.retry")}</button>
  </main>;
  if (!center) return <PageState />;

  return (
    <main className="page">
      <h1>{t("connections.title")}</h1>
      <p className="lead">{t("connections.subtitle")}</p>
      <p className="note">{t("connections.signedInAs", { email: identity?.email ?? t("connections.localIdentity") })} · {t("connections.workspace", { id: maskedWorkspace(session?.workspace_id ?? center.tenant_id) })}</p>
      <p className="note">{t("connections.privateHint")}</p>
      <p className="note">{t("connections.savedNotVerified")}</p>
      <p className="note">{t("environment.hint")}</p>
      <p className="note">{t("connections.currencyHint")}</p>
      {GROUPS.map((group) => {
        const items = group.providers
          .map((provider) => center.connections.find((item) => item.provider === provider))
          .filter((item): item is ConnectionStatus => Boolean(item));
        const ready = items.filter((item) => item.configured).length;
        return (
          <section className="section" key={group.title}>
            <div className="section-head">
              <div>
                <h2>{t(group.title)}</h2>
                <p className="muted">{t(group.hint)}</p>
              </div>
              <span className="muted">{t("connections.ready", { n: ready, total: items.length })}</span>
            </div>
            <ul className="rows">
              {items.map((item) => (
                <ConnectionRow item={item} key={item.provider} onChanged={refresh} />
              ))}
            </ul>
          </section>
        );
      })}
    </main>
  );
}

function ConnectionRow({ item, onChanged }: { item: ConnectionStatus; onChanged: () => Promise<void> }) {
  const { t, tx, locale } = useI18n();
  const { capabilities } = useDataMode();
  const [secret, setSecret] = useState("");
  const [vertex, setVertex] = useState({ projectId: item.project_id ?? "", location: item.location ?? "global", apiKey: "" });
  const [meta, setMeta] = useState({ accessToken: "", adAccountId: item.selected_account_id ?? "", apiVersion: item.api_version ?? "" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [test, setTest] = useState<ConnectionTestResult | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [currency, setCurrency] = useState(item.currency ?? "");
  const [formEpoch, setFormEpoch] = useState(0);
  const locked = busy || refreshFailed;
  const validCurrency = /^[A-Z]{3}$/.test(currency);

  const state = item.state === "connected" ? "connected" : item.state === "fallback_ready" ? "available" : "needsSetup";
  const stateLabel: Record<string, MessageKey> = {
    connected: "connections.connected",
    available: "connections.available",
    needsSetup: "connections.needsSetup",
  };

  async function act(work: () => Promise<unknown>, success?: string): Promise<boolean> {
    if (locked) return false;
    setBusy(true);
    setMessage("");
    setTest(null);
    try {
      await work();
      if (success) setMessage(success);
      try { await onChanged(); }
      catch { setRefreshFailed(true); }
      return true;
    } catch (caught) {
      setMessage(t(connectionError(caught)));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function refreshStatus() {
    setBusy(true);
    try { await onChanged(); setRefreshFailed(false); }
    catch { setRefreshFailed(true); }
    finally { setBusy(false); }
  }

  return (
    <li className="connection">
      <span className={`dot ${state === "needsSetup" ? "" : "ok"}`} aria-hidden="true" />
      <div className="row-main">
        <strong>{item.display_name}</strong>
        <small>{t(stateLabel[state])} · {tx(item.setup_label)}</small>
        <small>{t(`connections.storage.${item.storage_status ?? "unknown"}` as MessageKey)}</small>
        <small>{t("connections.savedAt", { time: formatTimestamp(item.saved_at, locale) })} · {t("connections.expiresAt", { time: formatTimestamp(item.expires_at, locale) })}</small>
        {item.supports_live_test && !test && <small>{t("connections.notChecked")}</small>}
        {BUSINESS_PROVIDERS.includes(item.provider) && <small>{t("connections.selectedCurrency", { currency: item.currency ?? t("connections.currencyUnset") })}</small>}
        {BUSINESS_PROVIDERS.includes(item.provider) && <small>{t(`environment.${test?.account_environment ?? item.account_environment ?? "unknown"}` as MessageKey)}</small>}
        <details>
          <summary>{t("common.details")}</summary>
          <p>{tx(item.note)}</p>
          <ol className="plain numbered">
            {item.setup_steps.map((step) => <li key={step}>{tx(step)}</li>)}
          </ol>

          {KEY_PROVIDERS.includes(item.provider) && (
            <form
              className="inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                act(
                  () => requestJson(`/api/v1/connections/${item.provider}/credential`, { method: "PUT", body: JSON.stringify({ secret, ...(item.provider === "stripe" ? { currency } : {}) }) }),
                  t("connections.saved"),
                ).then((saved) => {
                  if (saved) setSecret("");
                });
              }}
            >
              <label className="field">
                <span>{t(item.provider === "stripe" ? "connections.stripeKey" : "connections.key")}</span>
                <input autoComplete="new-password" disabled={locked} id={`key-${item.provider}`} onChange={(e) => setSecret(e.target.value)} type="password" value={secret} />
              </label>
              {item.provider === "stripe" && <CurrencyField value={currency} onChange={setCurrency} disabled={locked} provider={item.provider} />}
              <button className="button quiet" disabled={locked || !secret.trim() || (item.provider === "stripe" && !validCurrency)} type="submit">{t("connections.save")}</button>
            </form>
          )}

          {item.provider === "google_ads" && <GoogleAdsForm item={item} key={formEpoch} busy={locked} act={act} />}

          {item.provider === "gemini_vertex" && (
            <form
              className="stack-form"
              onSubmit={(event) => {
                event.preventDefault();
                act(
                  () =>
                    requestJson("/api/v1/connections/gemini_vertex/config", {
                      method: "PUT",
                      body: JSON.stringify({
                        project_id: vertex.projectId.trim(),
                        location: vertex.location.trim(),
                        ...(vertex.apiKey.trim() ? { api_key: vertex.apiKey.trim() } : {}),
                      }),
                    }),
                  t("connections.saved"),
                ).then((saved) => { if (saved) setVertex((current) => ({ ...current, apiKey: "" })); });
              }}
            >
              <label className="field"><span>{t("connections.projectId")}</span><input disabled={locked} id="vertex-project" onChange={(e) => setVertex({ ...vertex, projectId: e.target.value })} value={vertex.projectId} /></label>
              <label className="field"><span>{t("connections.location")}</span><input disabled={locked} id="vertex-location" onChange={(e) => setVertex({ ...vertex, location: e.target.value })} value={vertex.location} /></label>
              <label className="field"><span>{t(capabilities.live_self_service_enabled ? "connections.vertexKey" : "connections.optionalKey")}</span><input autoComplete="new-password" disabled={locked} id="vertex-key" onChange={(e) => setVertex({ ...vertex, apiKey: e.target.value })} type="password" value={vertex.apiKey} /></label>
              <button className="button quiet" disabled={locked || !vertex.projectId.trim() || (capabilities.live_self_service_enabled && !vertex.apiKey.trim())} type="submit">{t("connections.save")}</button>
            </form>
          )}

          {item.provider === "meta_ads" && (
            <form
              className="stack-form"
              onSubmit={(event) => {
                event.preventDefault();
                act(
                  () =>
                    requestJson("/api/v1/connections/meta_ads/config", {
                      method: "PUT",
                      body: JSON.stringify({
                        access_token: meta.accessToken.trim(),
                        ad_account_id: meta.adAccountId.trim(),
                        api_version: meta.apiVersion.trim(),
                        currency,
                      }),
                    }),
                  t("connections.saved"),
                ).then((saved) => {
                  if (saved) setMeta((current) => ({ ...current, accessToken: "" }));
                });
              }}
            >
              <label className="field">
                <span>{t("connections.metaToken")}</span>
                <input autoComplete="new-password" disabled={locked} id="meta-token" onChange={(e) => setMeta({ ...meta, accessToken: e.target.value })} type="password" value={meta.accessToken} />
              </label>
              <label className="field">
                <span>{t("connections.metaAccountId")}</span>
                <input disabled={locked} id="meta-account" onChange={(e) => setMeta({ ...meta, adAccountId: e.target.value })} value={meta.adAccountId} />
              </label>
              <label className="field">
                <span>{t("connections.metaApiVersion")}</span>
                <input disabled={locked} id="meta-version" onChange={(e) => setMeta({ ...meta, apiVersion: e.target.value })} value={meta.apiVersion} />
              </label>
              <CurrencyField value={currency} onChange={setCurrency} disabled={locked} provider={item.provider} />
              <button className="button quiet" disabled={locked || !validCurrency || !meta.accessToken.trim() || !meta.adAccountId.trim() || !meta.apiVersion.trim()} type="submit">{t("connections.save")}</button>
            </form>
          )}

          {!KEY_PROVIDERS.includes(item.provider) && item.provider !== "google_ads" && item.provider !== "gemini_vertex" && item.provider !== "meta_ads" && item.provider !== "reddit" && (
            <p className="muted">{t("connections.serverManaged")}</p>
          )}

          {item.accounts.length > 0 && (
            <label className="field">
              <span>{t("connections.account")}</span>
              <select
                disabled={locked}
                id={`account-${item.provider}`}
                onChange={(event) =>
                  act(() => requestJson(`/api/v1/connections/${item.provider}/account`, { method: "POST", body: JSON.stringify({ account_id: event.target.value }) }))
                }
                value={item.selected_account_id ?? ""}
              >
                {item.accounts.map((account) => <option key={account.account_id} value={account.account_id}>{account.label}</option>)}
              </select>
            </label>
          )}

          {item.supports_live_test && (
            <div className="actions-row">
              <button
                className="link"
                disabled={locked || !item.configured}
                onClick={() =>
                  act(async () => setTest(await requestJson<ConnectionTestResult>(`/api/v1/connections/${item.provider}/test`, { method: "POST" })))
                }
                type="button"
              >
                {busy ? t("connections.testing") : t("connections.test")}
              </button>
              {test && <span className={`note ${test.success ? "" : "warn"}`}>{t(test.success ? test.checked_live ? "connections.testPassed" : "connections.configuredOnly" : test.message.includes("account currency does not match") ? "connections.currencyMismatch" : test.message.includes("reporting timezone") ? "growth.live.timezoneUnavailable" : "connections.testFailed")}</span>}
              {test?.checked_at && <small>{t("connections.checkedAt", { time: formatTimestamp(test.checked_at, locale) })}</small>}
              {test && BUSINESS_PROVIDERS.includes(item.provider) && <small>{t(`environment.${test.account_environment ?? "unknown"}` as MessageKey)}</small>}
            </div>
          )}
          {item.configured && item.configuration_source !== "server_environment" && item.provider !== "reddit" && <div className="actions-row">
            <button className="link" disabled={locked} type="button" onClick={() => {
              act(() => requestJson(`/api/v1/connections/${item.provider}`, { method: "DELETE" }), t("connections.disconnected"))
                .then((done) => { if (done) { setSecret(""); setVertex((current) => ({ ...current, apiKey: "" })); setMeta((current) => ({ ...current, accessToken: "" })); setFormEpoch((value) => value + 1); } });
            }}>{t("connections.disconnect")}</button>
          </div>}
          {message && <p className="note">{message}</p>}
          {refreshFailed && <div role="status">
            <p className="note warn">{t("connections.refreshFailed")}</p>
            <button className="link" disabled={busy} onClick={() => { void refreshStatus(); }} type="button">{t("connections.retryStatus")}</button>
          </div>}
        </details>
      </div>
    </li>
  );
}

function CurrencyField({ value, onChange, disabled, provider }: {
  value: string; onChange: (value: string) => void; disabled: boolean; provider: string;
}) {
  const { t } = useI18n();
  return <label className="field">
    <span>{t("connections.currency")}</span>
    <input autoComplete="off" disabled={disabled} id={`currency-${provider}`} list={`currency-options-${provider}`} maxLength={3} minLength={3} pattern="[A-Za-z]{3}" placeholder="USD" required value={value} onChange={(event) => onChange(event.target.value.toUpperCase().replace(/[^A-Z]/g, ""))} />
    <datalist id={`currency-options-${provider}`}>
      {["USD", "EUR", "GBP", "CNY", "HKD", "TWD", "JPY", "KRW", "SGD", "AUD", "CAD", "NZD", "CHF", "INR", "IDR", "MYR", "THB", "VND", "PHP", "BRL", "MXN", "AED", "SAR", "ZAR"].map((code) => <option key={code} value={code} />)}
    </datalist>
    <small>{t("connections.currencyFieldHint")}</small>
  </label>;
}

function GoogleAdsForm({ item, busy, act }: {
  item: ConnectionStatus;
  busy: boolean;
  act: (work: () => Promise<unknown>, success?: string) => Promise<boolean>;
}) {
  const { t } = useI18n();
  const [method, setMethod] = useState<"oauth" | "access">("oauth");
  const [form, setForm] = useState({
    developer_token: "", customer_id: item.selected_account_id ?? "", login_customer_id: "",
    access_token: "", client_id: "", client_secret: "", refresh_token: "",
    currency: item.currency ?? "",
  });
  const change = (field: keyof typeof form, value: string) => setForm((current) => ({ ...current, [field]: value }));
  const complete = /^[A-Z]{3}$/.test(form.currency) && form.developer_token.trim() && form.customer_id.trim() && (method === "access"
    ? form.access_token.trim() : form.client_id.trim() && form.client_secret.trim() && form.refresh_token.trim());
  const field = (name: keyof typeof form, label: MessageKey, secret = false) => (
    <label className="field" key={name}>
      <span>{t(label)}</span>
      <input autoComplete={secret ? "new-password" : "off"} disabled={busy} id={`google-${name}`} type={secret ? "password" : "text"} value={form[name]} onChange={(event) => change(name, event.target.value)} />
    </label>
  );
  return (
    <form className="stack-form" onSubmit={(event) => {
      event.preventDefault();
      if (!complete || busy) return;
      const body = {
        developer_token: form.developer_token.trim(), customer_id: form.customer_id.replace(/[-\s]/g, ""),
        currency: form.currency,
        ...(form.login_customer_id.trim() ? { login_customer_id: form.login_customer_id.replace(/[-\s]/g, "") } : {}),
        ...(method === "access" ? { access_token: form.access_token.trim() } : {
          client_id: form.client_id.trim(), client_secret: form.client_secret.trim(), refresh_token: form.refresh_token.trim(),
        }),
      };
      act(() => requestJson("/api/v1/connections/google_ads/config", { method: "PUT", body: JSON.stringify(body) }), t("connections.saved"))
        .then((saved) => { if (saved) setForm((current) => ({ ...current, developer_token: "", access_token: "", client_secret: "", refresh_token: "" })); });
    }}>
      {field("developer_token", "connections.googleDeveloperToken", true)}
      {field("customer_id", "connections.googleCustomerId")}
      {field("login_customer_id", "connections.googleManagerId")}
      <CurrencyField value={form.currency} onChange={(value) => change("currency", value)} disabled={busy} provider="google_ads" />
      <Segmented label={t("connections.googleAuthMethod")} value={method} onChange={(next) => {
        setMethod(next);
        setForm((current) => ({ ...current, access_token: "", client_secret: "", refresh_token: "" }));
      }} options={[{ id: "oauth", label: t("connections.googleOAuth") }, { id: "access", label: t("connections.googleAccess") }]} />
      {method === "access" ? <>
        <p className="note">{t("connections.googleAccessHint")}</p>
        {field("access_token", "connections.googleAccessToken", true)}
      </> : <>
        {field("client_id", "connections.googleClientId")}
        {field("client_secret", "connections.googleClientSecret", true)}
        {field("refresh_token", "connections.googleRefreshToken", true)}
      </>}
      <button className="button quiet" disabled={busy || !complete} type="submit">{t("connections.save")}</button>
    </form>
  );
}
