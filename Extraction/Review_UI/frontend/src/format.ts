export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 10) return `${seconds.toFixed(2)}s`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtNumber(value: number | null | undefined, digits = 0): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

/** A percentage to two decimals ("54.90%"), or a dash when nothing is reviewed yet. */
export function fmtPct(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(2)}%`;
}

/** Pair counts behind an accuracy: right / (right + wrong + missed). */
export function fmtPairs(right: number, wrong: number, missed: number): string {
  return `${right} right · ${wrong} wrong · ${missed} missed`;
}

/** Detection details of a heading detector, beside its right / wrong / missed pairs. */
export function fmtHeadingDetail(score: { precision: number | null; recall: number | null; level_accuracy: number | null; pages: number }): string {
  const pct = (value: number | null) => (value == null ? "—" : `${value.toFixed(1)}%`);
  return `precision ${pct(score.precision)} · recall ${pct(score.recall)} · level ${pct(score.level_accuracy)} · ${score.pages} pages`;
}

export function accuracyTier(value: number): "good" | "warn" | "bad" {
  return value >= 90 ? "good" : value >= 70 ? "warn" : "bad";
}
