import { useEffect, useState } from "react";
import { api, SOURCE } from "../api.js";
import Icon from "../components/Icon.jsx";

const PAGE = 20;
const RATINGS = [["", "All"], ["2", "1–2★"], ["3", "Up to 3★"]];

export default function ReviewsTab({ week, themes = [], summary }) {
  const [f, setF] = useState({ theme: "", source: "", max_rating: "", q: "", sort: "helpful" });
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    const t = setTimeout(() => { setF((p) => ({ ...p, q })); setOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const params = { limit: PAGE, offset, sort: f.sort };
    for (const k of ["theme", "source", "max_rating", "q"]) if (f[k]) params[k] = f[k];
    api.reviews(week, params).then((r) => { setRes(r); setErr(null); }).catch((e) => setErr(e.message));
  }, [week, f, offset]);

  const pick = (k, v) => { setF((p) => ({ ...p, [k]: v })); setOffset(0); };
  const safeThemes = themes || [];
  const nameOf = (id) => (id === "other" ? "Other" : safeThemes.find((t) => t.theme_id === id)?.name || id);
  const bySource = summary?.reviews_by_source || {};
  const total = summary?.total_reviews;
  const filtered = !!(f.theme || f.source || f.max_rating || f.q);

  return (
    <>
      <div className="page-head">
        <div className="eyebrow">Corpus stream · Ingestion pipeline</div>
        <h1>Reviews Explorer</h1>
        <p>Search, filter and inspect verbatim feedback from Google Play and the Apple App Store. PII is scrubbed before anything is shown.</p>
      </div>

      <div className="row" style={{ marginBottom: 16 }}>
        {total != null && (
          <span className="mpill"><i className="dot ok" /><b>{total.toLocaleString()}</b> in this view</span>
        )}
        {res && <span className="mpill alt"><Icon name="filter_alt" sm /><b>{res.total.toLocaleString()}</b> {filtered ? "matching criteria" : "reviews"}</span>}
      </div>

      <div className="filterbox">
        <div className="filter-top">
          <div className="searchbox">
            <Icon name="search" sm />
            <input type="search" placeholder="Search verbatim feedback, e.g. 'UPI', 'KYC', 'chart'…" value={q} onChange={(e) => setQ(e.target.value)} />
            {q && <button className="icon-btn" title="Clear" onClick={() => setQ("")}><Icon name="close" sm /></button>}
          </div>
          <div className="chiprow">
            <span className="label-caps">Themes</span>
            <button className={`fchip ${f.theme === "" ? "on" : ""}`} onClick={() => pick("theme", "")}>All</button>
            {safeThemes.map((t) => (
              <button key={t.theme_id} className={`fchip ${f.theme === t.theme_id ? "on" : ""}`} onClick={() => pick("theme", t.theme_id)}>
                {f.theme === t.theme_id && <Icon name="check" sm />}{t.name}<em>{t.review_count}</em>
              </button>
            ))}
            <button className={`fchip ${f.theme === "other" ? "on" : ""}`} onClick={() => pick("theme", "other")}>Other</button>
          </div>
        </div>

        <div className="filter-bottom">
          <div className="seg" role="group" aria-label="Store">
            <button className={f.source === "" ? "on" : ""} onClick={() => pick("source", "")}>All stores</button>
            {["play_store", "app_store"].map((s) => (
              <button key={s} className={f.source === s ? "on" : ""} onClick={() => pick("source", s)}>
                {SOURCE[s]}{bySource[s] != null && <em className="cnt"> ({bySource[s].toLocaleString()})</em>}
              </button>
            ))}
          </div>
          <div className="chiprow">
            <span className="label-caps">Rating</span>
            {RATINGS.map(([v, l]) => (
              <button key={v} className={`fchip ${f.max_rating === v ? (v ? "on bad" : "on") : ""}`} onClick={() => pick("max_rating", v)}>{l}</button>
            ))}
          </div>
          <select value={f.sort} onChange={(e) => pick("sort", e.target.value)} aria-label="Sort">
            <option value="helpful">Most helpful</option>
            <option value="date">Newest</option>
            <option value="rating">Lowest rating</option>
          </select>
        </div>
      </div>

      {err && <div className="banner bad">{err}</div>}
      {!res ? (
        <div className="empty">Loading…</div>
      ) : res.items.length === 0 ? (
        <div className="empty">No reviews match.</div>
      ) : (
        <div className="card">
          {res.items.map((r) => (
            <div className="rev" key={r.rid}>
              <div className="meta">
                <span className={`chip ${r.rating <= 2 ? "bad" : r.rating >= 4 ? "ok" : "warn"}`}>{r.rating}★</span>
                <span className="chip">{SOURCE[r.source]}</span>
                <span className="chip">{nameOf(r.theme_id)}</span>
                <span className="chip">{r.date}</span>
                {r.helpful_votes > 0 && <span className="chip">{r.helpful_votes.toLocaleString()} helpful</span>}
              </div>
              {r.text}
            </div>
          ))}
        </div>
      )}
      {res && res.total > PAGE && (
        <div className="pager">
          <button className="btn ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
          <span>{offset + 1}–{Math.min(offset + PAGE, res.total)} of {res.total}</span>
          <button className="btn ghost" disabled={offset + PAGE >= res.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
        </div>
      )}
    </>
  );
}
