"use client";

import { useState } from "react";

import { useI18n } from "../lib/i18n";
import { useDataMode } from "./DataModeContext";

export function LiveSignIn() {
  const { identity, login, sessionError, retrySession, session, authError } = useDataMode();
  const { t } = useI18n();
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const registered = identity && !identity.anonymous;

  async function signIn() {
    setBusy(true);
    setError(false);
    try { await login(); } catch { setError(true); }
    finally { setBusy(false); }
  }

  return (
    <main className="page">
      <p className="eyebrow">{t("mode.live")}</p>
      <h1>{t(registered ? "auth.verifyTitle" : "auth.loginTitle")}</h1>
      <p className="lead">{t(registered ? "auth.verifyBody" : "auth.loginBody")}</p>
      <p className="note">{t("auth.readOnly")}</p>
      <div className="actions-row">
        <button className="button" disabled={busy} onClick={signIn} type="button">{t(busy ? "auth.signingIn" : registered ? "auth.switchAccount" : "auth.google")}</button>
        {(sessionError || (session?.authenticated && !session.live_allowed)) && <button className="button quiet" onClick={retrySession} type="button">{t("mode.retry")}</button>}
      </div>
      {(error || authError || sessionError) && <p className="outcome" role="alert">{t("auth.error")}</p>}
    </main>
  );
}
