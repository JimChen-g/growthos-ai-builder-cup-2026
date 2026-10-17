"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { API_BASE, type DataMode } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Segmented } from "./Segmented";

const STORAGE_KEY = "growthos-workspace-mode";

export type DeploymentCapabilities = { public_demo_only: boolean; live_available: boolean };
type DataModeContextValue = {
  mode: DataMode;
  setMode: (mode: DataMode) => void;
  capabilities: DeploymentCapabilities;
};
const DataModeContext = createContext<DataModeContextValue | null>(null);

export function DataModeProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [mode, updateMode] = useState<DataMode>("demo");
  const [capabilities, setCapabilities] = useState<DeploymentCapabilities | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const { t } = useI18n();

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    setFailed(false);

    // Resolve server policy before mounting pages, so a saved Live preference never
    // starts private-account requests against a public sample deployment.
    fetch(`${API_BASE}/api/v1/capabilities`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Workspace capabilities unavailable");
        const selected: unknown = await response.json();
        if (
          !selected || typeof selected !== "object" ||
          !("public_demo_only" in selected) || typeof selected.public_demo_only !== "boolean" ||
          !("live_available" in selected) || typeof selected.live_available !== "boolean" ||
          selected.public_demo_only === selected.live_available
        ) throw new Error("Invalid workspace capabilities");
        if (disposed) return;

        let restoredMode: DataMode = "demo";
        try {
          const saved = window.localStorage.getItem(STORAGE_KEY);
          if (saved === "live" && selected.live_available) restoredMode = "live";
          window.localStorage.setItem(STORAGE_KEY, restoredMode);
        } catch {
          /* storage unavailable */
        }
        updateMode(restoredMode);
        setCapabilities({ public_demo_only: selected.public_demo_only, live_available: selected.live_available });
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      })
      .finally(() => window.clearTimeout(timeout));

    return () => {
      disposed = true;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [attempt]);

  const setMode = useCallback((nextMode: DataMode) => {
    updateMode(nextMode === "live" && !capabilities?.live_available ? "demo" : nextMode);
  }, [capabilities]);

  useEffect(() => {
    if (!capabilities) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      /* storage unavailable */
    }
    document.documentElement.dataset.workspaceMode = mode;
  }, [mode, capabilities]);

  const value = useMemo(() => capabilities ? ({ mode, setMode, capabilities }) : null, [mode, setMode, capabilities]);
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
  if (!capabilities.live_available) return <strong className="workspace-fixed">{t("mode.publicDemo")}</strong>;
  return (
    <Segmented
      label={t("mode.label")}
      onChange={setMode}
      options={[
        { id: "demo", label: t("mode.demo") },
        { id: "live", label: t("mode.live") },
      ]}
      value={mode}
    />
  );
}
