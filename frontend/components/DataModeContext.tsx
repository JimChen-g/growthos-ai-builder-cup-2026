"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { API_BASE, isWorkspaceSession, requestJson, type DataMode, type WorkspaceSession } from "../lib/api";
import { observeFirebaseIdentity, signInWithGoogle, signOutFirebase, type FirebaseIdentity } from "../lib/firebase";
import { useI18n } from "../lib/i18n";
import { Segmented } from "./Segmented";

const STORAGE_KEY = "growthos-workspace-mode";
export type DeploymentCapabilities = { public_demo_only: boolean; live_available: boolean; live_self_service_enabled: boolean };
export type { WorkspaceSession } from "../lib/api";
type DataModeContextValue = {
  mode: DataMode;
  setMode: (mode: DataMode) => void;
  capabilities: DeploymentCapabilities;
  session: WorkspaceSession | null;
  sessionLoading: boolean;
  sessionError: boolean;
  authError: boolean;
  authPending: boolean;
  identity: FirebaseIdentity | null;
  identityKey: string;
  login: () => Promise<void>;
  logout: () => Promise<void>;
  retrySession: () => void;
};
const DataModeContext = createContext<DataModeContextValue | null>(null);

export function DataModeProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [mode, updateMode] = useState<DataMode>("demo");
  const [capabilities, setCapabilities] = useState<DeploymentCapabilities | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [identity, setIdentity] = useState<FirebaseIdentity | null | undefined>(undefined);
  const [session, setSession] = useState<WorkspaceSession | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [sessionError, setSessionError] = useState(false);
  const [authError, setAuthError] = useState(false);
  const [authPending, setAuthPending] = useState(false);
  const [sessionAttempt, setSessionAttempt] = useState(0);
  const { t } = useI18n();

  useEffect(() => observeFirebaseIdentity((nextIdentity) => {
    setSession(null);
    setSessionLoading(true);
    setSessionError(false);
    setIdentity(nextIdentity);
  }), []);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    let disposed = false;
    setFailed(false);
    fetch(`${API_BASE}/api/v1/capabilities`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Workspace capabilities unavailable");
        const selected = await response.json();
        if (typeof selected?.public_demo_only !== "boolean" || typeof selected?.live_available !== "boolean") {
          throw new Error("Invalid workspace capabilities");
        }
        if (disposed) return;
        const next = { ...selected, live_self_service_enabled: selected.live_self_service_enabled === true } as DeploymentCapabilities;
        let restoredMode: DataMode = "demo";
        try {
          if (window.localStorage.getItem(STORAGE_KEY) === "live" && (next.live_available || next.live_self_service_enabled)) restoredMode = "live";
        } catch { /* storage unavailable */ }
        updateMode(restoredMode);
        setCapabilities(next);
      })
      .catch(() => { if (!disposed) setFailed(true); })
      .finally(() => window.clearTimeout(timeout));
    return () => { disposed = true; window.clearTimeout(timeout); controller.abort(); };
  }, [attempt]);

  useEffect(() => {
    if (!capabilities || identity === undefined || authPending) return;
    const controller = new AbortController();
    let disposed = false;
    const timeout = window.setTimeout(() => {
      controller.abort();
      if (!disposed) {
        disposed = true;
        setSession(null);
        setSessionError(true);
        setSessionLoading(false);
      }
    }, 15000);
    setSessionLoading(true);
    setSessionError(false);
    requestJson<unknown>("/api/v1/me", { signal: controller.signal })
      .then((next) => {
        if (!isWorkspaceSession(next)) {
          throw new Error("Invalid workspace session");
        }
        if (!disposed) setSession(next);
      })
      .catch(() => { if (!disposed) { setSession(null); setSessionError(true); } })
      .finally(() => { window.clearTimeout(timeout); if (!disposed) setSessionLoading(false); });
    return () => { disposed = true; window.clearTimeout(timeout); controller.abort(); };
  }, [capabilities, identity, sessionAttempt, authPending]);

  const setMode = useCallback((nextMode: DataMode) => {
    const available = capabilities?.live_available || capabilities?.live_self_service_enabled;
    updateMode(nextMode === "live" && !available ? "demo" : nextMode);
  }, [capabilities]);
  const login = useCallback(async () => {
    setAuthError(false);
    setAuthPending(true);
    setSession(null);
    setSessionLoading(true);
    try { await signInWithGoogle(); }
    catch (error) { setAuthError(true); throw error; }
    finally { setAuthPending(false); setSessionAttempt((value) => value + 1); }
  }, []);
  const logout = useCallback(async () => {
    setAuthError(false);
    setSession(null);
    setSessionLoading(true);
    try { await signOutFirebase(); }
    catch (error) { setAuthError(true); throw error; }
    finally { setSessionAttempt((value) => value + 1); }
  }, []);
  const retrySession = useCallback(() => setSessionAttempt((value) => value + 1), []);

  useEffect(() => {
    if (!capabilities) return;
    try { window.localStorage.setItem(STORAGE_KEY, mode); } catch { /* storage unavailable */ }
    document.documentElement.dataset.workspaceMode = mode;
  }, [mode, capabilities]);

  const value = useMemo(() => capabilities ? ({
    mode, setMode, capabilities, session, sessionLoading, sessionError, authError, authPending, identity: identity ?? null,
    identityKey: `${identity?.uid ?? "guest"}:${session?.workspace_id ?? "pending"}:${mode}`,
    login, logout, retrySession,
  }) : null, [mode, setMode, capabilities, session, sessionLoading, sessionError, authError, authPending, identity, login, logout, retrySession]);
  if (failed) return (
    <main className="workspace-boot" role="alert">
      <h1>{t("mode.unavailableTitle")}</h1>
      <p className="lead">{t("mode.unavailableHint")}</p>
      <button className="button" type="button" onClick={() => setAttempt((current) => current + 1)}>{t("mode.retry")}</button>
    </main>
  );
  if (!value) return <div className="workspace-boot" role="status">{t("common.loading")}</div>;
  return <DataModeContext.Provider value={value}>{children}</DataModeContext.Provider>;
}

export function useDataMode(): DataModeContextValue {
  const context = useContext(DataModeContext);
  if (!context) throw new Error("useDataMode must be used inside DataModeProvider");
  return context;
}

export function WorkspaceModeSwitch() {
  const { mode, setMode, capabilities } = useDataMode();
  const { t } = useI18n();
  if (!capabilities.live_available && !capabilities.live_self_service_enabled) return <strong className="workspace-fixed">{t("mode.publicDemo")}</strong>;
  return <Segmented label={t("mode.label")} onChange={setMode} options={[
    { id: "demo", label: t("mode.demo") }, { id: "live", label: t("mode.live") },
  ]} value={mode} />;
}
