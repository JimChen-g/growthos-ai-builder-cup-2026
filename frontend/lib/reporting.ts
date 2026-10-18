/** Display provider values without replacing missing measurements with zero. */
export function formatMoney(value: number | string | null, currency: string, locale: string): string {
  if (value === null || value === "" || !Number.isFinite(Number(value))) return "—";
  const formatter = new Intl.NumberFormat(locale, {
    style: "currency", currency: currency.toUpperCase(), currencyDisplay: "code",
  });
  const amount = Number(value);
  const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2;
  // Monthly normalization and ad micros can yield sub-minor-unit amounts, even
  // for JPY/KRW. Preserve them instead of turning a real nonzero amount into 0.
  if ((digits === 0 && !Number.isInteger(amount)) || (amount !== 0 && Math.abs(amount) < 10 ** -digits)) {
    return new Intl.NumberFormat(locale, {
      style: "currency", currency: currency.toUpperCase(), currencyDisplay: "code",
      minimumFractionDigits: Math.max(2, digits), maximumFractionDigits: Math.max(6, digits),
    }).format(amount);
  }
  return formatter.format(amount);
}

/** Timestamps are explicitly UTC; browser timezone must not imply a source reporting timezone. */
export function formatTimestamp(value: string | null | undefined, locale: string): string {
  if (!value || !Number.isFinite(new Date(value).getTime())) return "—";
  return `${new Intl.DateTimeFormat(locale, {
    dateStyle: "medium", timeStyle: "short", timeZone: "UTC",
  }).format(new Date(value))} UTC`;
}

export function maskedWorkspace(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length > 10 ? `${value.slice(0, 6)}…${value.slice(-4)}` : `${value.slice(0, 3)}…${value.slice(-2)}`;
}
