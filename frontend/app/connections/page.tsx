"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useDataMode } from "../../components/DataModeContext";
import { PageState } from "../../components/PageState";
import {
  getJson,
  requestJson,
  type ConnectionCenter,
  type ConnectionStatus,
  type ConnectionTestResult,
  type ConnectorProvider,
} from "../../lib/api";
import { type MessageKey, useI18n } from "../../lib/i18n";

const GROUPS: Array<{ title: MessageKey; hint: MessageKey; providers: ConnectorProvider[] }> = [
  { title: "connections.business", hint: "connections.businessHint", providers: ["stripe", "google_ads", "meta_ads"] },
  { title: "connections.ai", hint: "connections.aiHint", providers: ["openai", "gemini", "gemini_vertex", "perplexity"] },
  { title: "connections.voice", hint: "connections.voiceHint", providers: ["x", "reddit"] },
];
const KEY_PROVIDERS: ConnectorProvider[] = ["openai", "gemini", "perplexity", "x"];

export default function ConnectionsPage() {
  const { mode } = useDataMode();
  const { t } = useI18n();
  const [center, setCenter] = useState<ConnectionCenter | null>(null);
  const [failed, setFailed] = useState(false);

  const refresh = () => getJson<ConnectionCenter>("/api/v1/connections").then(setCenter);

  useEffect(() => {
    if (mode !== "live") return;
    const controller = new AbortController();
    setFailed(false);
    setCenter(null);
    requestJson<ConnectionCenter>("/api/v1/connections", { signal: controller.signal })
      .then(setCenter)
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
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
  if (failed) return <PageState error />;
  if (!center) return <PageState />;

  return (
    <main className="page">
      <h1>{t("connections.title")}</h1>
      <p className="lead">{t("connections.subtitle")}</p>
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
                <ConnectionRow item={item} key={item.provider} onChanged={() => refresh().catch(() => undefined)} />
              ))}
            </ul>
          </section>
        );
      })}
    </main>
  );
}

function ConnectionRow({ item, onChanged }: { item: ConnectionStatus; onChanged: () => void }) {
  const { t, tx } = useI18n();
  const [secret, setSecret] = useState("");
  const [vertex, setVertex] = useState({ projectId: item.project_id ?? "", location: item.location ?? "global", apiKey: "" });
  const [meta, setMeta] = useState({ accessToken: "", adAccountId: item.selected_account_id ?? "", apiVersion: item.api_version ?? "" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [test, setTest] = useState<ConnectionTestResult | null>(null);

  const state = item.state === "connected" ? "connected" : item.state === "fallback_ready" ? "available" : "needsSetup";
  const stateLabel: Record<string, MessageKey> = {
    connected: "connections.connected",
    available: "connections.available",
    needsSetup: "connections.needsSetup",
  };

  async function act(work: () => Promise<unknown>, success?: string): Promise<boolean> {
    setBusy(true);
    setMessage("");
    setTest(null);
    try {
      await work();
      if (success) setMessage(success);
      onChanged();
      return true;
    } catch (caught) {
      setMessage(caught instanceof Error ? tx(caught.message) : t("common.error"));
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="connection">
      <span className={`dot ${state === "needsSetup" ? "" : "ok"}`} aria-hidden="true" />
      <div className="row-main">
        <strong>{item.display_name}</strong>
        <small>{t(stateLabel[state])} · {tx(item.setup_label)}</small>
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
                  () => requestJson(`/api/v1/connections/${item.provider}/credential`, { method: "PUT", body: JSON.stringify({ secret }) }),
                  t("connections.saved"),
                ).then((saved) => {
                  if (saved) setSecret("");
                });
              }}
            >
              <label className="field">
                <span>{t("connections.key")}</span>
                <input autoComplete="new-password" id={`key-${item.provider}`} onChange={(e) => setSecret(e.target.value)} type="password" value={secret} />
              </label>
              <button className="button quiet" disabled={busy || !secret.trim()} type="submit">{t("connections.save")}</button>
            </form>
          )}

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
                );
              }}
            >
              <label className="field"><span>{t("connections.projectId")}</span><input id="vertex-project" onChange={(e) => setVertex({ ...vertex, projectId: e.target.value })} value={vertex.projectId} /></label>
              <label className="field"><span>{t("connections.location")}</span><input id="vertex-location" onChange={(e) => setVertex({ ...vertex, location: e.target.value })} value={vertex.location} /></label>
              <label className="field"><span>{t("connections.optionalKey")}</span><input autoComplete="new-password" id="vertex-key" onChange={(e) => setVertex({ ...vertex, apiKey: e.target.value })} type="password" value={vertex.apiKey} /></label>
              <button className="button quiet" disabled={busy || !vertex.projectId.trim()} type="submit">{t("connections.save")}</button>
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
                <input autoComplete="new-password" disabled={busy} id="meta-token" onChange={(e) => setMeta({ ...meta, accessToken: e.target.value })} type="password" value={meta.accessToken} />
              </label>
              <label className="field">
                <span>{t("connections.metaAccountId")}</span>
                <input disabled={busy} id="meta-account" onChange={(e) => setMeta({ ...meta, adAccountId: e.target.value })} value={meta.adAccountId} />
              </label>
              <label className="field">
                <span>{t("connections.metaApiVersion")}</span>
                <input disabled={busy} id="meta-version" onChange={(e) => setMeta({ ...meta, apiVersion: e.target.value })} value={meta.apiVersion} />
              </label>
              <button className="button quiet" disabled={busy || !meta.accessToken.trim() || !meta.adAccountId.trim() || !meta.apiVersion.trim()} type="submit">{t("connections.save")}</button>
            </form>
          )}

          {!KEY_PROVIDERS.includes(item.provider) && item.provider !== "gemini_vertex" && item.provider !== "meta_ads" && item.provider !== "reddit" && (
            <p className="muted">{t("connections.serverManaged")}</p>
          )}

          {item.accounts.length > 0 && (
            <label className="field">
              <span>{t("connections.account")}</span>
              <select
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
                disabled={busy || !item.configured}
                onClick={() =>
                  act(async () => setTest(await requestJson<ConnectionTestResult>(`/api/v1/connections/${item.provider}/test`, { method: "POST" })))
                }
                type="button"
              >
                {busy ? t("connections.testing") : t("connections.test")}
              </button>
              {test && <span className={`note ${test.success ? "" : "warn"}`}>{tx(test.message)}</span>}
            </div>
          )}
          {message && <p className="note">{message}</p>}
        </details>
      </div>
    </li>
  );
}
