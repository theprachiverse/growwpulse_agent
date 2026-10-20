const STAR_COLORS = ["#dc2626", "#f08a3c", "#e6b422", "#7cc576", "#00b386"];

/** Share movement vs the previous week, in percentage points (falls back to the run's own WoW). */
export function TrendBadge({ theme }) {
  const d = theme.trend_pts ?? theme.wow_delta;
  if (d == null) return null;
  const flat = Math.abs(d) < 0.5;
  const cls = flat ? "flat" : d > 0 ? "up" : "down";
  return (
    <span className={`trend-badge ${cls}`} title="Change in share of reviews vs the previous week">
      {flat ? "•" : d > 0 ? "▲" : "▼"} {Math.abs(d).toFixed(1)} pts
    </span>
  );
}

/** Stacked 1★–5★ bar for a theme's reviews. */
export function RatingMix({ dist }) {
  const total = (dist || []).reduce((n, x) => n + x, 0);
  if (!total) return <span style={{ color: "var(--muted)" }}>–</span>;
  const title = dist.map((n, i) => `${i + 1}★ ${n}`).join(" · ");
  return (
    <span className="rating-mix" role="img" aria-label={`Rating mix: ${title}`} title={title}>
      {dist.map((n, i) => (
        <span key={i} style={{ width: `${(n / total) * 100}%`, background: STAR_COLORS[i] }} />
      ))}
    </span>
  );
}
