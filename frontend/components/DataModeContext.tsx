"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import type { DataMode } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { Segmented } from "./Segmented";

const STORAGE_KEY = "growthos-workspace-mode";

type DataModeContextValue = { mode: DataMode; setMode: (mode: DataMode) => void };
const DataModeContext = createContext<DataModeContextValue | null>(null);

export function DataModeProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [mode, setMode] = useState<DataMode>("demo");
  const [restored, setRestored] = useState(false);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === "demo" || saved === "live") setMode(saved);
    } catch {
      /* storage unavailable */
    }
    setRestored(true);
  }, []);

  useEffect(() => {
    if (!restored) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      /* storage unavailable */
    }
    document.documentElement.dataset.workspaceMode = mode;
  }, [mode, restored]);

  const value = useMemo(() => ({ mode, setMode }), [mode]);
  if (!restored) return <div className="boot" role="status" aria-label="GrowthOS" />;
  return <DataModeContext.Provider value={value}>{children}</DataModeContext.Provider>;
}

export function useDataMode(): DataModeContextValue {
  const context = useContext(DataModeContext);
  if (!context) throw new Error("useDataMode must be used inside DataModeProvider");
  return context;
}

export function WorkspaceModeSwitch() {
  const { mode, setMode } = useDataMode();
  const { t } = useI18n();
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
