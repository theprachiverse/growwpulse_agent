import { SOURCE, pct } from "../api.js";

const COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)"];

export default function ThemesTab({ data, onGenerate }) {
  const themes = [...(data?.themes || [])].sort((a, b) => b.review_count - a.review_count);
  const total = data?.pulse?.total_reviews || data?.week_summary?.total_reviews || (themes.reduce((n, t) => n + t.review_count, 0) || 1);
  const assigned = themes.reduce((n, t) => n + t.review_count, 0);
  const other = Math.max(0, total - assigned);
  const segs = [
    ...themes.map((t, i) => ({ id: t.theme_id, name: t.name, n: t.review_count, color: COLORS[i % 5] })),
    { id: "other", name: "Other", n: other, color: "var(--c0)" },
  ];
  const top3 = new Set((data?.pulse?.top_themes || themes.slice(0, 3)).map((t) => t.theme_id));

  if (themes.length === 0) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "40px 20px" }}>
        <h3>No Themes Generated for this Week</h3>
        <p style={{ color: "var(--muted)", maxWidth: 500, margin: "8px auto 16px" }}>
          Themes are discovered and clustered during the AI pipeline run. Run the pipeline to classify reviews into ≤5 product themes.
        </p>
        <button className="btn" onClick={() => onGenerate?.(data?.week)}>
          ⚡ Generate Pulse & Themes
        </button>
      </div>
    );
  }

  return (
    <>
      <div className="card">
        <strong>Share of {total.toLocaleString()} reviews</strong>
        <div className="bar" style={{ marginTop: 10 }} role="img" aria-label="Theme share of reviews">
          {segs.map((s) => (
            <span key={s.id} style={{ width: `${(s.n / total) * 100}%`, background: s.color }} title={`${s.name}: ${s.n}`} />
          ))}
        </div>
        <div className="legend">
          {segs.map((s) => (
            <span key={s.id}><i style={{ background: s.color }} />{s.name} · {pct(s.n / total)}</span>
          ))}
        </div>
        {other / total > 0.2 && (
          <div className="banner warn" style={{ marginTop: 12, marginBottom: 0 }}>
            “Other” is {pct(other / total)} of reviews, above the 20% target.
          </div>
        )}
      </div>

      <h2>Theme breakdown</h2>
      <div className="card" style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Theme</th><th className="num">Reviews</th><th className="num">Share</th>
              <th className="num">Avg ★</th><th className="num">1–2★</th>
              <th className="num">{SOURCE.play_store}</th><th className="num">{SOURCE.app_store}</th>
            </tr>
          </thead>
          <tbody>
            {themes.map((t) => (
              <tr key={t.theme_id}>
                <td>
                  <strong>{t.name}</strong>
                  {top3.has(t.theme_id) && <span className="chip ok" style={{ marginLeft: 8 }}>in pulse</span>}
                  <div style={{ color: "var(--muted)", fontSize: 13 }}>{t.definition}</div>
                </td>
                <td className="num">{t.review_count}</td>
                <td className="num">{pct(t.share)}</td>
                <td className="num">{t.avg_rating.toFixed(2)}</td>
                <td className="num">{pct(t.low_rating_share)}</td>
                <td className="num">{t.by_source.play_store ?? 0}</td>
                <td className="num">{t.by_source.app_store ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
