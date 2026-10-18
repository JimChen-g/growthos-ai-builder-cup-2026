"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import serverHans from "./server.zh-Hans.json";
import serverHant from "./server.zh-Hant.json";
import uiEn from "./ui.en.json";
import uiHans from "./ui.zh-Hans.json";
import uiHant from "./ui.zh-Hant.json";

export type Language = "en" | "zh-Hans" | "zh-Hant";
export const LANGUAGES: Array<{ id: Language; label: string }> = [
  { id: "en", label: "EN" },
  { id: "zh-Hans", label: "简体" },
  { id: "zh-Hant", label: "繁體" },
];
export type MessageKey = keyof typeof uiEn;
type ServerPack = { exact: Record<string, string>; tpl: Record<string, string> };

const UI: Record<Language, Record<string, string>> = { en: uiEn, "zh-Hans": uiHans, "zh-Hant": uiHant };
const SERVER: Record<Exclude<Language, "en">, ServerPack> = { "zh-Hans": serverHans, "zh-Hant": serverHant };
const LOCALE: Record<Language, string> = { en: "en-US", "zh-Hans": "zh-CN", "zh-Hant": "zh-TW" };
const STORAGE_KEY = "growthos-language";

function fill(template: string, vars: Record<string, string | number> = {}): string {
  return template.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? `{${name}}`));
}

function detect(): Language {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "en" || saved === "zh-Hans" || saved === "zh-Hant") return saved;
  } catch {
    /* storage unavailable */
  }
  const preferred = (navigator.language || "en").toLowerCase();
  if (/^zh-(tw|hk|mo|hant)/.test(preferred)) return "zh-Hant";
  if (preferred.startsWith("zh")) return "zh-Hans";
  return "en";
}

/** Translate text produced by the API (agent replies, labels, ledger reasons). */
export function translateServerText(text: string, lang: Language): string {
  if (lang === "en" || !text) return text;
  const pack = SERVER[lang];
  const exact = (value: string) => pack.exact[value] ?? pack.exact[value.toLowerCase()] ?? value;
  const tpl = (name: string, vars: Record<string, string | number>) => fill(pack.tpl[name], vars);
  const list = (value: string) => {
    const items = value.split(/, | and /).map((item) => exact(item.trim()));
    return items.length <= 1
      ? items.join("")
      : `${items.slice(0, -1).join(pack.tpl.listSep)}${pack.tpl.listAnd}${items.at(-1)}`;
  };
  const band = (value: string) => {
    const key = `band.${value.toLowerCase()}`;
    return UI[lang][key] ?? exact(value);
  };
  const finding = (value: string): string => {
    let m = value.match(/^(Google Ads|Meta Ads): (.+) \((.+)\)$/);
    if (m) return tpl("findingAds", { platform: `${m[1]} `, problem: exact(m[2]), reason: exact(m[3]) });
    m = value.match(/^AI visibility of (\d+)\/100 \((\w+)\)$/);
    if (m) return tpl("findingGeo", { score: m[1], status: exact(m[2]) });
    m = value.match(/^(Google Ads|Meta Ads) (.+)$/);
    if (m) return `${m[1]} ${exact(m[2])}`;
    return exact(value);
  };
  if (pack.exact[text]) return pack.exact[text];

  const rules: Array<[RegExp, (m: RegExpMatchArray) => string]> = [
    [
      /^Growth health is ([\d.]+)\/100 \((\w+)\)\. (?:Period revenue is (up|down) ([\d.]+%)|Period revenue is flat) vs\. prior reporting period, while (.+) remains the main risk\.$/,
      (m) => tpl("execSummary", {
        score: Math.round(Number(m[1])), band: band(m[2]),
        momentum: m[3] ? tpl(m[3] === "up" ? "periodRevUp" : "periodRevDown", { pct: m[4] }) : pack.tpl.periodRevFlat,
        risk: finding(m[5]),
      }),
    ],
    [
      /^Growth health is ([\d.]+)\/100 \((\w+)\)\. (?:Revenue is (up|down) ([\d.]+%)|Revenue is flat), while (.+) remains the main risk\.$/,
      (m) =>
        tpl("execSummary", {
          score: Math.round(Number(m[1])),
          band: band(m[2]),
          momentum: m[3] ? tpl(m[3] === "up" ? "revUp" : "revDown", { pct: m[4] }) : pack.tpl.revFlat,
          risk: finding(m[5]),
        }),
    ],
    [/^Prioritize (.+) to address the clearest unowned growth opportunity\.$/, (m) => tpl("recReason", { rec: exact(m[1]) })],
    [
      /^You raised (.+?); the connected sources don't include that data yet, so I can't diagnose it reliably\.(?: (.*))?$/,
      (m) => [tpl("ackUncovered", { topics: list(m[1]) }), m[2] ? translateServerText(m[2], lang) : ""].join(""),
    ],
    [/^On (.+?), the data points to (.+)\.$/, (m) => tpl("ackCovered", { topics: list(m[1]), finding: finding(m[2]) })],
    [/^What the connected data does show is (.+)\.$/, (m) => tpl("ackNone", { finding: finding(m[1]) })],
    [/^([+-][\d.]+%) (vs\. yesterday|vs\. prior check)$/, (m) => tpl("trendSince", { context: exact(m[2]), value: m[1] })],
    [/^([\d.]+%) positive across Reddit \+ X$/, (m) => tpl("positiveAcross", { pct: m[1] })],
    [/^Current ROAS: (.+)$/, (m) => tpl("currentRoas", { v: m[1] })],
    [/^Current CAC: (.+)$/, (m) => tpl("currentCac", { v: m[1] })],
    [/^CAC increased ([\d.]+%) versus the prior period$/, (m) => tpl("cacUp", { v: m[1] })],
    [/^CAC decreased ([\d.]+%) versus the prior period$/, (m) => tpl("cacDown", { v: m[1] })],
    [/^ROAS increased ([\d.]+%) versus the prior period$/, (m) => tpl("roasUp", { v: m[1] })],
    [/^ROAS declined ([\d.]+%) versus the prior period$/, (m) => tpl("roasDown", { v: m[1] })],
    [/^Observed ROAS does not meet the ([\d.]+) target$/, (m) => tpl("roasTarget", { v: m[1] })],
    [
      /^(CAC increased|ROAS declined|ROAS is below target|Scaling efficiency needs validation): (.+)$/,
      (m) => tpl("problemReason", { problem: exact(m[1]), reason: exact(m[2]) }),
    ],
    [/^Brand appeared in (\d+) of (\d+) tracked prompts$/, (m) => tpl("appeared", { m: m[1], n: m[2] })],
    [/^(\d+) brand mentions and (\d+) owned citations$/, (m) => tpl("visibilityHistorySummary", { n: m[1], m: m[2] })],
    [/^Improve CAC — (.+): (.+?)(?: Failure: (.+))?$/, (m) => {
      const base = tpl("reasonCac", { problem: exact(m[1]), reason: exact(m[2]) });
      return m[3] ? tpl("failure", { text: base, error: m[3] }) : base;
    }],
    [
      /^Measure (.+) visibility and identify the next search-presence opportunity; current score is (\d+)\/100\.$/,
      (m) => tpl("reasonGeo", { brand: m[1], score: m[2] }),
    ],
    [/^Surface customer intelligence — main problem: (.+)\.$/, (m) => tpl("reasonFeedback", { problem: exact(m[1]) })],
    [/^Read current public Reddit and X signals for (.+)\.$/, (m) => tpl("reasonCollect", { brand: m[1] })],
    [/^Measured (.+) across the selected connected AI sources\.$/, (m) => tpl("reasonScan", { brand: m[1] })],
    [/^Created a new growth baseline from connected revenue/, () => pack.tpl.reasonBaseline],
    [/^Measured Growth Score ([\d.]+)\/100$/, (m) => tpl("actionMeasured", { score: Math.round(Number(m[1])) })],
    [/^live mode is missing configuration: (.+)$/, (m) => tpl("missingConfig", { list: m[1] })],
  ];
  for (const [pattern, render] of rules) {
    const match = text.match(pattern);
    if (match) return render(match);
  }
  return exact(text);
}

type I18nValue = {
  lang: Language;
  setLang: (lang: Language) => void;
  t: (key: MessageKey, vars?: Record<string, string | number>) => string;
  tx: (text: string) => string;
  locale: string;
};

const I18nContext = createContext<I18nValue | null>(null);

export function LanguageProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [lang, setLang] = useState<Language>("en");

  useEffect(() => {
    setLang(detect());
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
    try {
      window.localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      /* storage unavailable */
    }
  }, [lang]);

  const value = useMemo<I18nValue>(
    () => ({
      lang,
      setLang,
      locale: LOCALE[lang],
      t: (key, vars) => fill(UI[lang][key] ?? uiEn[key] ?? key, vars),
      tx: (text) => translateServerText(text, lang),
    }),
    [lang],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const context = useContext(I18nContext);
  if (!context) throw new Error("useI18n must be used inside LanguageProvider");
  return context;
}
