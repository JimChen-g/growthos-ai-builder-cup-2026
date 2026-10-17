"use client";

import { useState } from "react";

import type { AgentActionLedger, AuditIntegrityStatus } from "../lib/api";
import { useI18n } from "../lib/i18n";

export function StatusPill({ status }: { status: string }) {
  const { t } = useI18n();
  const key = `status.${status}` as Parameters<typeof t>[0];
  return <span className={`pill pill-${status}`}>{t(key)}</span>;
}

function ProofLine({ integrity }: { integrity: AuditIntegrityStatus }) {
  const { t, locale } = useI18n();
  const block = integrity.block_number?.toLocaleString(locale) ?? "";
  if (integrity.state === "verified") {
    const pending = integrity.pending_record_count ?? 0;
    return (
      <div className="proof proof-ok">
        <span className="dot" aria-hidden="true" />
        <div>
          <strong>{t("proof.verified")}</strong>
          <small>
            {t("proof.verifiedDetail", { n: integrity.record_count, block })}
            {pending > 0 ? ` · ${t("proof.pending", { n: pending })}` : ""}
          </small>
        </div>
        {integrity.explorer_url && (
          <a href={integrity.explorer_url} rel="noreferrer" target="_blank">
            {t("proof.view")} ↗
          </a>
        )}
      </div>
    );
  }
  if (integrity.state === "mismatch") {
    return (
      <div className="proof proof-bad">
        <span className="dot" aria-hidden="true" />
        <div>
          <strong>{t("proof.mismatch")}</strong>
          <small>{t("proof.mismatchDetail")}</small>
        </div>
      </div>
    );
  }
  return (
    <div className="proof">
      <span className="dot" aria-hidden="true" />
      <div>
        <strong>
          {integrity.state === "empty"
            ? t("proof.empty")
            : t("proof.notAnchored", { n: integrity.record_count })}
        </strong>
      </div>
    </div>
  );
}

export function ActionLedger({
  ledger,
  integrity,
  limit = 5,
}: {
  ledger: AgentActionLedger;
  integrity: AuditIntegrityStatus;
  limit?: number;
}) {
  const { t, tx, locale } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const time = new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  const entries = expanded ? ledger.entries : ledger.entries.slice(0, limit);

  return (
    <section className="section" aria-labelledby="record-heading">
      <div className="section-head">
        <h2 id="record-heading">{t("ledger.title")}</h2>
        <span className="muted">{t("ledger.count", { n: ledger.total })}</span>
      </div>
      <ProofLine integrity={integrity} />
      {entries.length === 0 ? (
        <p className="muted">{t("ledger.empty")}</p>
      ) : (
        <ol className="rows record">
          {entries.map((entry) => (
            <li key={entry.entry_id}>
              <div className="row-main">
                <strong>{tx(entry.action)}</strong>
                <small>
                  {tx(entry.agent)} · <time dateTime={entry.timestamp}>{time.format(new Date(entry.timestamp))}</time>
                </small>
                <p>{tx(entry.reason)}</p>
              </div>
              <StatusPill status={entry.status} />
            </li>
          ))}
        </ol>
      )}
      {ledger.entries.length > limit && (
        <button className="link" onClick={() => setExpanded((value) => !value)} type="button">
          {expanded ? t("common.showLess") : t("common.showAll")}
        </button>
      )}
    </section>
  );
}
