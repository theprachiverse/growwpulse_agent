import { SOURCE, pct } from "../api.js";

const themeName = (themes, id) => themes.find((t) => t.theme_id === id)?.name || id;

export default function PulseTab({ data, onGenerate, onSwitchWeek, onSwitchTab }) {
  const { pulse, week_summary } = data;
  const partial = data.manifest?.coverage_start?.app_store;

  if (!pulse) {
    const summary = week_summary || {};
    const totalReviews = summary.total_reviews || 0;
    const avgRating = summary.avg_rating != null ? `${summary.avg_rating.toFixed(2)} ★` : "–";

    return (
      <>
        <div className="grid stats">
          <Stat
            label="Reviews Collected"
            value={totalReviews.toLocaleString()}
            note={summary.window_start && summary.window_end ? `${summary.window_start} → ${summary.window_end}` : data.week}
          />
          <Stat label="Avg Rating" value={avgRating} />
          {Object.entries(summary.reviews_by_source || {}).map(([s, n]) => (
            <Stat
              key={s}
              label={SOURCE[s]}
              value={n.toLocaleString()}
              note={summary.avg_rating_by_source?.[s] != null ? `${summary.avg_rating_by_source[s]?.toFixed(2)} ★ avg` : undefined}
            />
          ))}
          <Stat label="Pulse Status" value="Pending" note="Click below to generate" />
        </div>

        <div className="card" style={{ padding: "20px", marginBottom: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
            <div>
              <h2 style={{ margin: "0 0 6px", fontSize: 17 }}>
                {totalReviews} Reviews available for {data.label || data.week}
              </h2>
              <p style={{ margin: "0 0 14px", color: "var(--muted)", fontSize: 14 }}>
                The raw reviews for this week are collected and scrubbed. The 1-page executive pulse (top 3 themes, 3 verbatim quotes, 3 action ideas) has not been synthesized for this individual week yet.
              </p>
            </div>
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button className="btn" onClick={() => onGenerate?.(data.week)}>
              ⚡ Generate Pulse for {data.week}
            </button>
            <button className="btn ghost" onClick={() => onSwitchTab?.("reviews")}>
              📑 View All {totalReviews} Reviews
            </button>
            <button className="btn ghost" onClick={() => onSwitchWeek?.("all")}>
              📊 View All Reviews Till Now (10-Week Window)
            </button>
          </div>
        </div>

        {data.themes && data.themes.length > 0 && (
          <>
            <h2>Themes Breakdown for {data.label || data.week}</h2>
            <div className="grid">
              {data.themes.map((t, i) => (
                <div className="card theme" key={t.theme_id}>
                  <div className="rank">{i + 1}</div>
                  <div>
                    <h3>{t.name}</h3>
                    <p>{t.definition}</p>
                    <div className="chips">
                      <span className="chip">{t.review_count} reviews ({pct(t.share)})</span>
                      <span className={`chip ${t.low_rating_share > 0.6 ? "bad" : t.low_rating_share > 0.4 ? "warn" : ""}`}>
                        {pct(t.low_rating_share)} are 1–2★
                      </span>
                      <span className="chip">{t.avg_rating.toFixed(2)} ★</span>
                      {Object.entries(t.by_source || {}).map(([s, n]) => (
                        <span key={s} className="chip">{SOURCE[s]}: {n}</span>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </>
    );
  }

  return (
    <>
      <div className="grid stats">
        <Stat label="Reviews" value={pulse.total_reviews.toLocaleString()} note={`${pulse.window_start} → ${pulse.window_end}`} />
        <Stat label="Avg rating" value={`${pulse.avg_rating.toFixed(2)} ★`} />
        {Object.entries(pulse.reviews_by_source).map(([s, n]) => (
          <Stat key={s} label={SOURCE[s]} value={n.toLocaleString()} note={`${pulse.avg_rating_by_source[s]?.toFixed(2)} ★ avg`} />
        ))}
        <Stat label="Word count" value={pulse.word_count} note="limit 250" />
      </div>

      {partial && <div className="banner warn">App Store coverage starts {partial}; older weeks come from stored snapshots.</div>}

      <h2>Top themes</h2>
      <div className="grid">
        {pulse.top_themes.map((t, i) => (
          <div className="card theme" key={t.theme_id}>
            <div className="rank">{i + 1}</div>
            <div>
              <h3>{t.name}</h3>
              <p>{t.definition}</p>
              <div className="chips">
                <span className="chip">{pct(t.share)} of reviews</span>
                <span className={`chip ${t.low_rating_share > 0.6 ? "bad" : t.low_rating_share > 0.4 ? "warn" : ""}`}>
                  {pct(t.low_rating_share)} are 1–2★
                </span>
                <span className="chip">{t.avg_rating.toFixed(2)} ★</span>
                {Object.entries(t.by_source).map(([s, n]) => (
                  <span key={s} className="chip">{SOURCE[s]}: {n}</span>
                ))}
                {t.wow_delta != null && (
                  <span className={`chip ${t.wow_delta > 0 ? "bad" : "ok"}`}>
                    {t.wow_delta > 0 ? "+" : ""}{(t.wow_delta * 100).toFixed(1)} pts WoW
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      <h2>What users said</h2>
      <div className="grid">
        {pulse.quotes.map((q) => (
          <div className="card" key={q.rid}>
            <blockquote>
              “{q.text}”
              <div className="meta">
                {q.rating}★ · {SOURCE[q.source]} · {q.month} · {themeName(pulse.top_themes, q.theme_id)}
              </div>
            </blockquote>
          </div>
        ))}
      </div>

      <h2>Action ideas</h2>
      <div className="grid">
        {pulse.actions.map((a) => (
          <div className="card" key={a.title}>
            <strong>{a.title}</strong>
            <p style={{ margin: "4px 0 8px" }}>{a.rationale}</p>
            <div className="chips">
              <span className="chip ok">{a.owner_hint}</span>
              <span className="chip">{themeName(pulse.top_themes, a.theme_id)}</span>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function Stat({ label, value, note }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="val">{value}</div>
      {note && <div className="note">{note}</div>}
    </div>
  );
}
