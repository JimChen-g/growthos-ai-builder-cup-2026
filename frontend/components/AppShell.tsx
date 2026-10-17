"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { type MessageKey, useI18n } from "../lib/i18n";
import { useDataMode, WorkspaceModeSwitch } from "./DataModeContext";
import { LanguageSwitch } from "./LanguageSwitch";

const navigation: Array<{ href: string; label: MessageKey; liveOnly?: boolean; sampleOnly?: boolean }> = [
  { href: "/builder-cup", label: "nav.builderCup", sampleOnly: true },
  { href: "/", label: "nav.today" },
  { href: "/dashboard", label: "nav.growth" },
  { href: "/agent", label: "nav.actions" },
  { href: "/customer-intelligence", label: "nav.customers" },
  { href: "/visibility", label: "nav.visibility" },
  { href: "/connections", label: "nav.connections", liveOnly: true },
];

export function AppShell({ children, footer }: Readonly<{ children: React.ReactNode; footer?: React.ReactNode }>) {
  const pathname = usePathname().replace(/\/+$/, "") || "/";
  const { mode, capabilities } = useDataMode();
  const { t } = useI18n();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const items = navigation.filter((item) =>
    (!item.liveOnly || (mode === "live" && capabilities.live_available)) &&
    (!item.sampleOnly || mode === "demo")
  );
  const unavailableConnections = pathname === "/connections" && !capabilities.live_available;

  // Close the phone settings sheet whenever the page changes.
  useEffect(() => setSettingsOpen(false), [pathname]);

  const links = (className?: string) =>
    items.map((item) => (
      <Link
        aria-current={pathname === item.href ? "page" : undefined}
        className={className}
        href={item.href}
        key={item.href}
      >
        {t(item.label)}
      </Link>
    ));

  const controls = (
    <>
      <div className="control">
        <span>{t("mode.label")}</span>
        <WorkspaceModeSwitch />
        <small>{capabilities.public_demo_only ? t("mode.publicDemoHint") : mode === "demo" ? t("mode.demoHint") : t("mode.liveHint")}</small>
      </div>
      <div className="control">
        <span>{t("lang.label")}</span>
        <LanguageSwitch />
      </div>
    </>
  );

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link className="brand" href="/">
          <span className="brand-mark" aria-hidden="true" />
          <span>
            <strong>GrowthOS</strong>
            <small>{t("brand.tagline")}</small>
          </span>
        </Link>
        <nav aria-label="Primary">{links()}</nav>
        <div className="sidebar-foot">{controls}</div>
      </aside>

      <header className="topbar">
        <Link className="brand" href="/">
          <span className="brand-mark" aria-hidden="true" />
          <strong>GrowthOS</strong>
        </Link>
        <span className={`mode-chip mode-${mode}`}>{capabilities.public_demo_only ? t("mode.publicDemo") : mode === "demo" ? t("mode.demo") : t("mode.live")}</span>
        <button
          aria-controls="settings-sheet"
          aria-expanded={settingsOpen}
          aria-label={t("shell.settings")}
          className="icon-button"
          onClick={() => setSettingsOpen((open) => !open)}
          type="button"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20">
            <circle cx="5" cy="12" r="1.8" fill="currentColor" />
            <circle cx="12" cy="12" r="1.8" fill="currentColor" />
            <circle cx="19" cy="12" r="1.8" fill="currentColor" />
          </svg>
        </button>
      </header>
      {settingsOpen && (
        <>
          <button aria-label={t("shell.close")} className="sheet-scrim" onClick={() => setSettingsOpen(false)} type="button" />
          <div className="sheet" id="settings-sheet" role="dialog" aria-label={t("shell.settings")}>
            {controls}
          </div>
        </>
      )}

      <div className="content" key={mode}>
        {unavailableConnections ? (
          <main className="page">
            <h1>{t("mode.publicConnectionsTitle")}</h1>
            <p className="lead">{t("mode.publicConnectionsHint")}</p>
            <div className="section">
              <Link className="button" href="/builder-cup">{t("nav.builderCup")}</Link>
            </div>
          </main>
        ) : children}
        {footer}
      </div>

      <nav aria-label="Primary" className="tabbar" style={{ "--tabs": items.length } as React.CSSProperties}>
        {links("tab")}
      </nav>
    </div>
  );
}
