import { useEffect, useState } from "react";
import { api, SOURCE } from "../api.js";
import Icon from "../components/Icon.jsx";

const PAGE = 20;
const RATINGS = [
  { id: "all", label: "All ratings", params: {} },
  { id: "pos", label: "4★ & above · Positive", params: { min_rating: 4 } },
  { id: "mid", label: "3★ · Neutral", params: { min_rating: 3, max_rating: 3 } },
  { id: "neg", label: "1–2★ · Negative", params: { max_rating: 2 } },
  { id: "low", label: "3★ & below · Needs attention", params: { max_rating: 3 } },
];
const SORTS = [
  ["helpful", "Most helpful"],
  ["date", "Newest first"],
  ["rating", "Lowest rating first"],
];
const EMPTY = { theme: "", source: "", rating: "all", q: "", sort: "helpful" };

export default function ReviewsTab({ week, themes = [], summary }) {
  const [f, setF] = useState(EMPTY);
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    const t = setTimeout(() => { setF((p) => ({ ...p, q: q.trim() })); setOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const params = { limit: PAGE, offset, sort: f.sort };
    for (const k of ["theme", "source", "q"]) if (f[k]) params[k] = f[k];
    Object.assign(params, RATINGS.find((r) => r.id === f.rating)?.params);
    api.reviews(week, params).then((r) => { setRes(r); setErr(null); }).catch((e) => setErr(e.message));
  }, [week, f, offset]);

  const pick = (k, v) => { setF((p) => ({ ...p, [k]: v })); setOffset(0); };
  const reset = () => { setF(EMPTY); setQ(""); setOffset(0); };
  const safeThemes = themes || [];
  const nameOf = (id) => (id === "other" ? "Other" : safeThemes.find((t) => t.theme_id === id)?.name || id);
  const bySource = summary?.reviews_by_source || {};
  const filtered = !!(f.theme || f.source || f.rating !== "all" || f.q);
  const pages = res ? Math.max(1, Math.ceil(res.total / PAGE)) : 1;
  const page = Math.floor(offset / PAGE) + 1;

  return (
    <>
      <div className="page-head">
        <div className="eyebrow">Customer voice</div>
        <h1>Reviews Explorer</h1>
        <p>Search, filter and read verbatim feedback from Google Play and the Apple App Store. Personal data is scrubbed before anything is shown.</p>
      </div>

      <div className="filterbox">
        <div className="filter-top">
          <div className="searchbox">
            <Icon name="search" sm />
            <input type="text" placeholder="Search reviews, e.g. “UPI”, “KYC”, “chart”…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search reviews" />
            {q && <button className="icon-btn" title="Clear search" aria-label="Clear search" onClick={() => setQ("")}><Icon name="close" sm /></button>}
          </div>
          <div className="filter-selects">
            <label className="sel">
              <span className="label-caps">Rating</span>
              <select value={f.rating} onChange={(e) => pick("rating", e.target.value)}>
                {RATINGS.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
              </select>
            </label>
            <label className="sel">
              <span className="label-caps">Sort by</span>
              <select value={f.sort} onChange={(e) => pick("sort", e.target.value)}>
                {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          </div>
        </div>

        <div className="filter-bottom">
          <div className="chiprow">
            <span className="label-caps">Theme</span>
            <button className={`fchip ${f.theme === "" ? "on" : ""}`} onClick={() => pick("theme", "")}>All</button>
            {safeThemes.map((t) => (
              <button key={t.theme_id} className={`fchip ${f.theme === t.theme_id ? "on" : ""}`} onClick={() => pick("theme", t.theme_id)}>
                {f.theme === t.theme_id && <Icon name="check" sm />}{t.name}<em>{t.review_count}</em>
              </button>
            ))}
            <button className={`fchip ${f.theme === "other" ? "on" : ""}`} onClick={() => pick("theme", "other")}>Other</button>
          </div>
          <div className="seg" role="group" aria-label="Store">
            <button className={f.source === "" ? "on" : ""} onClick={() => pick("source", "")}>All stores</button>
            {["play_store", "app_store"].map((s) => (
              <button key={s} className={f.source === s ? "on" : ""} onClick={() => pick("source", s)}>
                {SOURCE[s]}{bySource[s] != null && <em className="cnt"> {bySource[s].toLocaleString()}</em>}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="result-bar">
        <span>
          {res ? <><b>{res.total.toLocaleString()}</b> {res.total === 1 ? "review" : "reviews"}{filtered ? " match your filters" : ""}</> : "Loading…"}
        </span>
        {filtered && (
          <button className="theme-view-link" onClick={reset}><Icon name="restart_alt" sm /> Reset filters</button>
        )}
      </div>

      {err && <div className="banner bad">{err}</div>}
      {!res ? (
        <div className="empty">Loading…</div>
      ) : res.items.length === 0 ? (
        <div className="empty">
          No reviews match these filters.
          {filtered && <div style={{ marginTop: 10 }}><button className="btn ghost sm" onClick={reset}>Reset filters</button></div>}
        </div>
      ) : (
        <div className="rev-list">
          {res.items.map((r) => (
            <article className="rev" key={r.rid}>
              <div className="meta">
                <span className={`chip rate ${r.rating <= 2 ? "bad" : r.rating >= 4 ? "ok" : "warn"}`}>{r.rating}★</span>
                <span className="chip">{SOURCE[r.source]}</span>
                <span className="chip">{nameOf(r.theme_id)}</span>
                <span className="when">{r.date}</span>
                {r.helpful_votes > 0 && (
                  <span className="helpful"><Icon name="thumb_up" sm />{r.helpful_votes.toLocaleString()} helpful</span>
                )}
              </div>
              <p>{r.text}</p>
            </article>
          ))}
        </div>
      )}
      {res && res.total > PAGE && (
        <div className="pager">
          <button className="btn ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
          <span>Page {page} of {pages} · {offset + 1}–{Math.min(offset + PAGE, res.total)} of {res.total.toLocaleString()}</span>
          <button className="btn ghost" disabled={offset + PAGE >= res.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
        </div>
      )}
    </>
  );
}
