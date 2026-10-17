"use client";

import { useI18n } from "../lib/i18n";

export function PageState({ error = false }: { error?: boolean }) {
  const { t } = useI18n();
  return (
    <main className="page state" role="status">
      <span className={error ? "breath still" : "breath"} aria-hidden="true" />
      <p>{error ? t("common.error") : t("common.loading")}</p>
    </main>
  );
}
