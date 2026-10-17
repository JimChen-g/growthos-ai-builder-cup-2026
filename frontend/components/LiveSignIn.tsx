"use client";

import { useI18n } from "../lib/i18n";
import { useDataMode } from "./DataModeContext";

export function LiveSignIn() {
  const { identity, login, sessionError, sessionExpired, sessionLoading, retrySession, session, authError, authPending } = useDataMode();
  const { t } = useI18n();
  const registered = identity && !identity.anonymous;
  const busy = authPending || sessionLoading;

  return (
    <main className="page">
      <p className="eyebrow">{t("mode.live")}</p>
      <h1>{t(sessionExpired ? "auth.expiredTitle" : registered ? "auth.verifyTitle" : "auth.loginTitle")}</h1>
      <p className="lead">{t(sessionExpired ? "auth.expiredBody" : registered ? "auth.verifyBody" : "auth.loginBody")}</p>
      <p className="note">{t("auth.readOnly")}</p>
      <div className="actions-row">
        <button className="button" disabled={busy} onClick={() => { login().catch(() => undefined); }} type="button">{t(authPending ? "auth.signingIn" : sessionLoading ? "auth.checking" : sessionExpired ? "auth.reauth" : registered ? "auth.switchAccount" : "auth.google")}</button>
        {(sessionError || (session?.authenticated && !session.live_allowed)) && <button className="button quiet" disabled={busy} onClick={retrySession} type="button">{t("mode.retry")}</button>}
      </div>
      {authPending && <p className="outcome" role="status">{t("auth.popupPending")}</p>}
      {(authPending || authError) && <p className="note">{t("auth.browserHint")}</p>}
      {(authError || (sessionError && !sessionExpired)) && !authPending && <p className="outcome" role="alert">{t("auth.error")}</p>}
    </main>
  );
}
