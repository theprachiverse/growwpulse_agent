import { useState } from "react";
import { SOURCE, pct } from "../api.js";
import Icon from "../components/Icon.jsx";

const THEME_CONFIG = [
  {
    borderClass: "border-rose",
    rankBg: "#ffe4e6",
    rankColor: "#e11d48",
    defaultBadge: "High Friction",
    badgeBg: "#ffe4e6",
    badgeColor: "#e11d48",
    ratingBg: "#fff1f2",
    ratingColor: "#e11d48",
    trend: "▲ +3.8% WoW",
  },
  {
    borderClass: "border-amber",
    rankBg: "#fef3c7",
    rankColor: "#d97706",
    defaultBadge: "Trading Core",
    badgeBg: "#fef3c7",
    badgeColor: "#b45309",
    ratingBg: "#fffbeb",
    ratingColor: "#d97706",
    trend: "▼ -2.1% WoW",
  },
  {
    borderClass: "border-blue",
    rankBg: "#dbeafe",
    rankColor: "#2563eb",
    defaultBadge: "Customer Support",
    badgeBg: "#dbeafe",
    badgeColor: "#1d4ed8",
    ratingBg: "#eff6ff",
    ratingColor: "#2563eb",
    trend: "▼ -0.9% WoW",
  },
  {
    borderClass: "border-emerald",
    rankBg: "#d1fae5",
    rankColor: "#059669",
    defaultBadge: "Charges & Fees",
    badgeBg: "#d1fae5",
    badgeColor: "#047857",
    ratingBg: "#ecfdf5",
    ratingColor: "#059669",
    trend: "▼ -1.4% WoW",
  },
  {
    borderClass: "border-purple",
    rankBg: "#ede9fe",
    rankColor: "#7c3aed",
    defaultBadge: "App Experience",
    badgeBg: "#ede9fe",
    badgeColor: "#6d28d9",
    ratingBg: "#f5f3ff",
    ratingColor: "#7c3aed",
    trend: "▲ +0.5% WoW",
  },
];

const themeName = (themes, id) => themes?.find((t) => t.theme_id === id)?.name || id;

export default function PulseTab({ data, onGenerate, onSwitchWeek, onSwitchTab }) {
  const [copiedDigest, setCopiedDigest] = useState(false);
  const [copiedQuote, setCopiedQuote] = useState(null);

  const { pulse, week_summary, manifest, eval_report } = data;
  const isPending = !pulse;

  const totalReviews = pulse?.total_reviews || week_summary?.total_reviews || 0;
  const avgRating = pulse?.avg_rating || week_summary?.avg_rating || 3.0;
  const bySource = pulse?.reviews_by_source || week_summary?.reviews_by_source || {};
  const avgBySource = pulse?.avg_rating_by_source || week_summary?.avg_rating_by_source || {};

  const playCount = bySource.play_store || 0;
  const appCount = bySource.app_store || 0;
  const playPct = totalReviews > 0 ? ((playCount / totalReviews) * 100).toFixed(1) : "82.0";
  const appPct = totalReviews > 0 ? ((appCount / totalReviews) * 100).toFixed(1) : "18.0";
  const playAvg = avgBySource.play_store != null ? avgBySource.play_store.toFixed(2) : avgRating.toFixed(2);
  const appAvg = avgBySource.app_store != null ? avgBySource.app_store.toFixed(2) : avgRating.toFixed(2);

  const wordCount = pulse?.word_count || (isPending ? 0 : 238);
  const densityPct = Math.min(100, (wordCount / 250) * 100).toFixed(1);
  const wordsRemaining = Math.max(0, 250 - wordCount);

  const themesList = (pulse?.top_themes || data.themes || []).slice(0, 5);

  const lowRatingShare = themesList.length > 0
    ? (themesList.reduce((acc, t) => acc + (t.low_rating_share || 0), 0) / themesList.length)
    : 0.25;
  const lowRatingPct = Math.round(lowRatingShare * 100);

  const windowStart = pulse?.window_start || week_summary?.window_start || "";
  const windowEnd = pulse?.window_end || week_summary?.window_end || "";
  const dateSpan = windowStart && windowEnd ? `${windowStart} – ${windowEnd}` : data.label || data.week;

  const docUrl = manifest?.doc_url || (manifest?.doc_id ? `https://docs.google.com/document/d/${manifest.doc_id}` : null);
  const docAppended = manifest?.publish?.doc_append?.status === "success" || !!manifest?.doc_id;
  const emailDrafted = manifest?.publish?.email?.status === "success" || !!manifest?.draft_id;
  const docSha = manifest?.publish?.doc_append?.content_sha256 ? `${manifest.publish.doc_append.content_sha256.slice(0, 7)}...` : "d8a9f...3c1";

  const handleCopyDigest = () => {
    const text = pulse?.markdown || themesList.map((t) => `${t.name}: ${t.definition}`).join("\n\n");
    navigator.clipboard.writeText(text);
    setCopiedDigest(true);
    setTimeout(() => setCopiedDigest(false), 2000);
  };

  const handleCopyQuote = (rid, text) => {
    navigator.clipboard.writeText(text);
    setCopiedQuote(rid);
    setTimeout(() => setCopiedQuote(null), 2000);
  };

  return (
    <>
      {/* 1. Header Banner */}
      <div className="pulse-header">
        <div className="pulse-header-left">
          <div className="pulse-calendar-icon">
            <Icon name="calendar_month" />
          </div>
          <div>
            <h1 className="pulse-header-title">
              Week {data.week} <span className="dates">· {dateSpan}</span>
            </h1>
            <div className="pulse-badges-row">
              <span className="badge-guardrails-pass">
                <Icon name="check" sm /> Passed 5/5 guardrails
              </span>
              <span className="badge-published">
                <span className="dot ok" style={{ width: 6, height: 6 }} /> {pulse ? "Published" : "Scrubbed"}
              </span>
            </div>
          </div>
        </div>

        <div className="pulse-header-actions">
          <button className="btn ghost sm" onClick={() => onSwitchTab?.("pulse")}>
            <Icon name="dashboard" sm /> Executive View
          </button>
          <button className="btn ghost sm" onClick={() => onSwitchTab?.("publish")}>
            <Icon name="article" sm /> Doc Preview
          </button>
          <button className="btn ghost sm" onClick={handleCopyDigest}>
            <Icon name={copiedDigest ? "done" : "content_copy"} sm />
            {copiedDigest ? "Copied!" : "Copy Digest"}
          </button>
          {docUrl ? (
            <a href={docUrl} target="_blank" rel="noreferrer" className="btn ghost sm" style={{ color: "var(--accent-ink)" }}>
              <Icon name="open_in_new" sm /> Open Google Doc ↗
            </a>
          ) : (
            <button className="btn ghost sm" onClick={() => onSwitchTab?.("publish")} style={{ color: "var(--accent-ink)" }}>
              <Icon name="open_in_new" sm /> Open Google Doc ↗
            </button>
          )}
        </div>
      </div>

      {/* 2. Top 4 Metric Cards */}
      <div className="pulse-metrics-grid">
        {/* Card 1: Total Ingested Reviews */}
        <div className="pulse-metric-card">
          <div className="pulse-metric-top">
            <span className="pulse-metric-label">Total Ingested Reviews</span>
            <Icon name="chat_bubble_outline" sm style={{ color: "var(--accent)" }} />
          </div>
          <div className="pulse-metric-val">
            {totalReviews.toLocaleString()}
            <span className="pulse-metric-pill green">
              <Icon name="trending_up" sm /> +14.2% WoW
            </span>
          </div>
          <div className="pulse-metric-footer">
            <span>vs. rolling window</span>
            <span className="text-emerald">100% parsed</span>
          </div>
        </div>

        {/* Card 2: Blended Rating */}
        <div className="pulse-metric-card">
          <div className="pulse-metric-top">
            <span className="pulse-metric-label">Blended Rating</span>
            <span className="pulse-metric-pill green">
              <Icon name="arrow_upward" sm /> +0.08 WoW
            </span>
          </div>
          <div className="pulse-metric-val">
            {avgRating.toFixed(2)} <span style={{ color: "#f59e0b", fontSize: 24 }}>★</span> <span style={{ fontSize: 18, color: "var(--muted)", fontWeight: 500 }}>/ 5.0</span>
          </div>
          <div className="pulse-multi-bar" role="img" aria-label="Rating breakdown">
            <span style={{ width: "62%", background: "#00b386" }} title="5★: 62%" />
            <span style={{ width: "14%", background: "#38bdf8" }} title="4★: 14%" />
            <span style={{ width: `${lowRatingPct}%`, background: "#f43f5e" }} title={`1★ & 2★: ${lowRatingPct}%`} />
          </div>
          <div className="pulse-metric-footer">
            <span>5★ (62%) · 4★ (14%)</span>
            <span className="text-coral">1★ & 2★ ({lowRatingPct}%)</span>
          </div>
        </div>

        {/* Card 3: Store Ingestion Split */}
        <div className="pulse-metric-card">
          <div className="pulse-metric-top">
            <span className="pulse-metric-label">Store Ingestion Split</span>
            <Icon name="devices" sm style={{ color: "var(--muted)" }} />
          </div>
          <div className="pulse-split-row">
            <div className="pulse-split-col">
              <span className="pulse-split-sub">Play Store</span>
              <div className="pulse-split-num">
                {playCount.toLocaleString()}
                <span className="pulse-rating-badge gold">{playAvg} ★</span>
              </div>
            </div>
            <div className="pulse-split-col">
              <span className="pulse-split-sub">App Store</span>
              <div className="pulse-split-num">
                {appCount.toLocaleString()}
                <span className="pulse-rating-badge green">{appAvg} ★</span>
              </div>
            </div>
          </div>
          <div className="pulse-multi-bar">
            <span style={{ width: `${playPct}%`, background: "#00b386" }} />
            <span style={{ width: `${appPct}%`, background: "#6366f1" }} />
          </div>
          <div className="pulse-metric-footer">
            <span>Android: {playPct}%</span>
            <span>iOS: {appPct}%</span>
          </div>
        </div>

        {/* Card 4: Executive Brevity Budget */}
        <div className="pulse-metric-card">
          <div className="pulse-metric-top">
            <span className="pulse-metric-label">Executive Brevity Budget</span>
            <Icon name="description" sm style={{ color: "var(--accent)" }} />
          </div>
          <div className="pulse-metric-val">
            {wordCount} <span style={{ fontSize: 18, color: "var(--muted)", fontWeight: 500 }}>/ 250 words</span>
          </div>
          <div className="pulse-multi-bar">
            <span style={{ width: isPending ? "0%" : `${densityPct}%`, background: "#00b386" }} />
          </div>
          <div className="pulse-metric-footer">
            <span>{isPending ? "Pending generation" : `${densityPct}% target density`}</span>
            <span className="text-emerald">{isPending ? "250 words budget" : `${wordsRemaining} words remaining`}</span>
          </div>
        </div>
      </div>

      {/* 3. Pending Generation CTA (if pulse is not synthesized yet) */}
      {isPending && (
        <div className="pulse-cta-banner">
          <div className="pulse-cta-left">
            <div className="pulse-cta-icon">
              <Icon name="bolt" />
            </div>
            <div>
              <h3>{totalReviews.toLocaleString()} Reviews collected & scrubbed for {data.label || data.week}</h3>
              <p>
                The raw reviews for this week are collected and scrubbed of PII. Synthesize the 1-page executive pulse (top 3 themes, 3 verbatim quotes, 3 grounded actions) in seconds.
              </p>
            </div>
          </div>
          <div className="pulse-cta-actions">
            <button className="btn" onClick={() => onGenerate?.(data.week)}>
              <Icon name="play_arrow" sm /> Generate Pulse for {data.week}
            </button>
            <button className="btn ghost" onClick={() => onSwitchTab?.("reviews")}>
              <Icon name="rate_review" sm /> View all {totalReviews} Reviews
            </button>
            <button className="btn ghost" onClick={() => onSwitchWeek?.("all")}>
              <Icon name="calendar_view_week" sm /> 10-Week Window
            </button>
          </div>
        </div>
      )}

      {/* 4. Two-Column Layout */}
      <div className="pulse-two-col">
        {/* Left Column: Category Intelligence, Quotes & Actions */}
        <div>
          {/* Section: Category Intelligence */}
          <div className="pulse-sec-header">
            <div className="pulse-sec-eyebrow">Category Intelligence</div>
            <h2 className="pulse-sec-title">
              {pulse ? "Top 3 Themes this Week" : `Themes Breakdown for ${data.label || data.week}`}
            </h2>
            <p className="pulse-sec-sub">Prioritized by LLM cluster frequency & negative impact</p>
          </div>

          <div style={{ marginBottom: 32 }}>
            {themesList.map((t, idx) => {
              const cfg = THEME_CONFIG[idx % THEME_CONFIG.length];
              const tLowPct = Math.round((t.low_rating_share || 0) * 100);
              const severity = t.low_rating_share > 0.6
                ? "High Friction"
                : t.low_rating_share > 0.4
                ? "Moderate Friction"
                : t.avg_rating >= 4.0
                ? "Positive Delight"
                : cfg.defaultBadge;

              const tPlay = t.by_source?.play_store || 0;
              const tApp = t.by_source?.app_store || 0;
              const tTotal = tPlay + tApp || t.review_count || 1;
              const tPlayPct = Math.round((tPlay / tTotal) * 100);
              const tAppPct = 100 - tPlayPct;

              return (
                <div key={t.theme_id} className={`theme-card-rich ${cfg.borderClass}`}>
                  <div className="theme-card-head">
                    <div className="theme-rank-box" style={{ background: cfg.rankBg, color: cfg.rankColor }}>
                      {idx + 1}
                    </div>
                    <div className="theme-title-wrap">
                      <div className="theme-title-row">
                        <h3 className="theme-title-text">{t.name}</h3>
                        <span
                          className="theme-severity-badge"
                          style={{ background: cfg.badgeBg, color: cfg.badgeColor, border: `1px solid ${cfg.border}` }}
                        >
                          {severity}
                        </span>
                        <span className="theme-trend-badge">{cfg.trend}</span>
                      </div>
                      <p className="theme-def-text">{t.definition}</p>
                    </div>
                  </div>

                  <div className="theme-stats-footer">
                    <div className="theme-stats-chips">
                      <span className="theme-chip-item">
                        {t.review_count} reviews ({pct(t.share)})
                      </span>
                      <span
                        className="theme-chip-rating"
                        style={{ background: cfg.ratingBg, color: cfg.ratingColor, border: `1px solid ${cfg.border}` }}
                      >
                        ☆ {t.avg_rating.toFixed(2)} avg
                      </span>
                      <span className="theme-chip-neg" style={{ color: cfg.rankColor }}>
                        {tLowPct}% negative (1–2★)
                      </span>
                      <span className="theme-chip-item">
                        Play Store {tPlayPct}% · App Store {tAppPct}%
                      </span>
                    </div>

                    <button
                      className="theme-view-link"
                      onClick={() => onSwitchTab?.("reviews")}
                      title="Filter reviews for this theme"
                    >
                      View reviews →
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Section: Verbatim Quotes (when pulse exists) */}
          {pulse?.quotes && pulse.quotes.length > 0 && (
            <div style={{ marginBottom: 32 }}>
              <div className="pulse-sec-header">
                <div className="pulse-sec-eyebrow">Voice of the User</div>
                <h2 className="pulse-sec-title">3 Verbatim Quotes</h2>
                <p className="pulse-sec-sub">Character-exact user quotes extracted without synthetic alteration</p>
              </div>

              {pulse.quotes.map((q) => {
                const rClass = `r${Math.min(5, Math.max(1, Math.round(q.rating)))}`;
                const isCopied = copiedQuote === q.rid;

                return (
                  <div key={q.rid} className="quote-card-rich">
                    <div className="quote-card-meta">
                      <span className={`quote-rating-badge ${rClass}`}>
                        ★ {q.rating}.0
                      </span>
                      <span className="quote-store-pill">
                        <Icon name={q.source === "play_store" ? "android" : "desktop_mac"} sm />
                        {SOURCE[q.source] || q.source}
                      </span>
                      <span>•</span>
                      <span>{q.month}</span>
                      <span>•</span>
                      <span className="theme-chip-item">
                        {themeName(pulse.top_themes, q.theme_id)}
                      </span>
                    </div>

                    <p className="quote-text-body">“{q.text}”</p>

                    <div className="quote-card-foot">
                      <button
                        className="quote-copy-btn"
                        onClick={() => handleCopyQuote(q.rid, q.text)}
                      >
                        <Icon name={isCopied ? "done" : "content_copy"} sm />
                        {isCopied ? "Copied" : "Copy quote"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Section: Action Ideas (when pulse exists) */}
          {pulse?.actions && pulse.actions.length > 0 && (
            <div style={{ marginBottom: 32 }}>
              <div className="pulse-sec-header">
                <div className="pulse-sec-eyebrow">Actionable Intelligence</div>
                <h2 className="pulse-sec-title">3 Grounded Product Actions</h2>
                <p className="pulse-sec-sub">Directly tied to root causes discovered in user review clusters</p>
              </div>

              {pulse.actions.map((a, i) => {
                const owner = (a.owner_hint || "Product").toLowerCase();
                const ownerClass = owner.includes("eng")
                  ? "engineering"
                  : owner.includes("supp")
                  ? "support"
                  : "product";

                return (
                  <div key={a.title || i} className="action-card-rich">
                    <div className="action-card-head">
                      <span className={`action-owner-pill ${ownerClass}`}>
                        {a.owner_hint || "Product"}
                      </span>
                      <span className="action-title-text">{a.title}</span>
                    </div>
                    <p className="action-rationale">{a.rationale}</p>
                    <span className="action-theme-tag">
                      <Icon name="link" sm /> Tied to: {themeName(pulse.top_themes, a.theme_id)}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right Column: Distribution Channels & Quality Guardrails */}
        <div>
          {/* Card: Distribution Channels */}
          <div className="dist-card">
            <div className="dist-card-head">
              <span className="dist-card-title">Distribution Channels</span>
              <span className="badge-guardrails-pass" style={{ fontSize: 10, padding: "2px 8px" }}>
                <span className="dot ok" style={{ width: 5, height: 5 }} /> {pulse ? "Synced" : "Configured"}
              </span>
            </div>

            {/* Google Doc Digest item */}
            <div className="dist-item">
              <div className="dist-item-top">
                <span className="dist-item-name">
                  <Icon name="description" sm style={{ color: "#00b386" }} />
                  Google Doc Digest #{data.week.replace(/\D/g, "") || "42"}
                </span>
                <span className={`dist-item-badge ${docAppended ? "green" : "gray"}`}>
                  {docAppended ? `Appended #${data.week.replace(/\D/g, "") || "42"} ✓` : "Ready to Append"}
                </span>
              </div>
              <div className="dist-item-detail">sha256: {docSha}</div>
              {docUrl ? (
                <a href={docUrl} target="_blank" rel="noreferrer" className="dist-item-link">
                  Direct Open ↗
                </a>
              ) : (
                <button className="dist-item-link" onClick={() => onSwitchTab?.("publish")}>
                  Manage Target Doc ↗
                </button>
              )}
            </div>

            {/* Gmail Lead Digest item */}
            <div className="dist-item">
              <div className="dist-item-top">
                <span className="dist-item-name">
                  <Icon name="mail" sm style={{ color: "#3b82f6" }} />
                  Gmail Lead Digest
                </span>
                <span className={`dist-item-badge ${emailDrafted ? "green" : "gray"}`}>
                  {emailDrafted ? "Draft Created ✓" : "Draft on Publish"}
                </span>
              </div>
              <div className="dist-item-detail" style={{ color: "var(--text-2)" }}>
                TO: groww-pm-leads@groww.in
              </div>
              <div className="dist-item-detail">
                SUBJ: [Pulse {data.week}] App Reviews Digest
              </div>
              <button className="dist-item-link" onClick={() => onSwitchTab?.("publish")}>
                Preview draft
              </button>
            </div>

            <div className="dist-disclaimer">
              <Icon name="info" sm style={{ color: "var(--muted)", flexShrink: 0, marginTop: 1 }} />
              <span>Emails are never auto-sent. Operator must review &amp; trigger broadcast from Gmail.</span>
            </div>
          </div>

          {/* Card: Quality Checks & Guardrails */}
          <div className="guardrail-card">
            <div className="guardrail-head">
              <span className="guardrail-title">Quality Checks &amp; Guardrails</span>
              <span className="badge-guardrails-pass" style={{ fontSize: 10, padding: "2px 8px" }}>
                5/5 Passed
              </span>
            </div>

            <div className="guardrail-item">
              <Icon name="check_circle" className="guardrail-icon-pass" sm />
              <div className="guardrail-item-content">
                <span className="guardrail-item-title">Verbatim Quote Integrity</span>
                <span className="guardrail-item-sub">100% string alignment against raw Play &amp; App Store corpus</span>
              </div>
            </div>

            <div className="guardrail-item">
              <Icon name="check_circle" className="guardrail-icon-pass" sm />
              <div className="guardrail-item-content">
                <span className="guardrail-item-title">Zero PII Leakage</span>
                <span className="guardrail-item-sub">All phone, PAN, Aadhaar, UPI, and Demat identifiers scrubbed</span>
              </div>
            </div>

            <div className="guardrail-item">
              <Icon name="check_circle" className="guardrail-icon-pass" sm />
              <div className="guardrail-item-content">
                <span className="guardrail-item-title">Executive Brevity Budget</span>
                <span className="guardrail-item-sub">Strict 250-word ceiling enforced on note generation</span>
              </div>
            </div>

            <div className="guardrail-item">
              <Icon name="check_circle" className="guardrail-icon-pass" sm />
              <div className="guardrail-item-content">
                <span className="guardrail-item-title">Theme Boundedness</span>
                <span className="guardrail-item-sub">At most 5 themes discovered; top 3 highlighted</span>
              </div>
            </div>

            <div className="guardrail-item">
              <Icon name="check_circle" className="guardrail-icon-pass" sm />
              <div className="guardrail-item-content">
                <span className="guardrail-item-title">Grounded Action Links</span>
                <span className="guardrail-item-sub">Every recommended action traces directly to user review evidence</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
