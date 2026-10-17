"use client";

import { LANGUAGES, useI18n } from "../lib/i18n";
import { Segmented } from "./Segmented";

export function LanguageSwitch() {
  const { lang, setLang, t } = useI18n();
  return <Segmented label={t("lang.label")} onChange={setLang} options={LANGUAGES} value={lang} />;
}
