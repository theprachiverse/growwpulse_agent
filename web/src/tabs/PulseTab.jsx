import { useEffect, useState } from "react";
import { SOURCE, api, pct } from "../api.js";
import Icon from "../components/Icon.jsx";
import { RatingMix, TrendBadge } from "../components/ThemeStats.jsx";

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
  },
];

const themeName = (themes, id) => themes?.find((t) => t.theme_id === id)?.name || id;

export default function PulseTab({ data, onGenerate, onSwitchWeek, onSwitchTab }) {
  const [copiedDigest, setCopiedDigest] = useState(false);
  const [copiedQuote, setCopiedQuote] = useState(null);
  const [pub, setPub] = useState(null);
  const [settings, setSettings] = useState(null);

  const { pulse, week_summary, manifest, eval_report } = data;
  const isPending = !pulse;
  const pulseWeek = data.pulse_week || data.week;

  // Real delivery status lives in publish_state.json, not the run manifest.
  useEffect(() => {
    let live = true;
    setPub(null);
    api.publishState(pulseWeek).then((r) => live && setPub(r?.state || null)).catch(() => {});
    return () => { live = false; };
  }, [pulseWeek]);
  useEffect(() => {
    api.publishSettings().then(setSettings).catch(() => {});
  }, []);

  const totalReviews = pulse?.total_reviews || week_summary?.total_reviews || 0;
  const avgRating = pulse?.avg_rating ?? week_summary?.avg_rating ?? null;
  const bySource = pulse?.reviews_by_source || week_summary?.reviews_by_source || {};
  const avgBySource = pulse?.avg_rating_by_source || week_summary?.avg_rating_by_source || {};

  const playCount = bySource.play_store || 0;
  const appCount = bySource.app_store || 0;
  const playPct = totalReviews > 0 ? ((playCount / totalReviews) * 100).toFixed(1) : "0.0";
  const appPct = totalReviews > 0 ? ((appCount / totalReviews) * 100).toFixed(1) : "0.0";
  const playAvg = avgBySource.play_store != null ? avgBySource.play_store.toFixed(2) : "—";
  const appAvg = avgBySource.app_store != null ? avgBySource.app_store.toFixed(2) : "—";

  const maxWords = eval_report?.max_words_allowed || 250;
  const wordCount = pulse?.word_count || 0;
  const densityPct = Math.min(100, (wordCount / maxWords) * 100).toFixed(1);
  const wordsRemaining = Math.max(0, maxWords - wordCount);

  const themesList = (pulse?.top_themes || data.themes || []).slice(0, 5);

  // Review-weighted 1–2★ share across the listed themes.
  const themedReviews = themesList.reduce((acc, t) => acc + (t.review_count || 0), 0);
  const lowRatingPct = themedReviews > 0
    ? Math.round((themesList.reduce((acc, t) => acc + (t.low_rating_share || 0) * (t.review_count || 0), 0) / themedReviews) * 100)
    : 0;

  const windowStart = pulse?.window_start || week_summary?.window_start || "";
  const windowEnd = pulse?.window_end || week_summary?.window_end || "";
  const dateSpan = windowStart && windowEnd ? `${windowStart} – ${windowEnd}` : data.label || data.week;

  const docUrl = pub?.doc_url || manifest?.doc_url || null;
  const docDone = pub?.doc_append?.status === "done";
  const docFailed = pub?.doc_append?.status === "failed";
  const emailDone = pub?.email?.status === "done";
  const emailMode = pub?.email?.mode;
  const published = docDone || emailDone;
  const docSha = pub?.doc_append?.content_sha256 ? `${pub.doc_append.content_sha256.slice(0, 7)}…` : null;

  // The guardrails the problem statement requires (§4.3, §6, §8).
  const CHECKS = [
    ["verbatim", "Verbatim Quotes", "Every quote is copied word for word from a real review"],
    ["pii", "No PII", "No usernames, emails, device IDs or other reviewer identifiers"],
    ["word_limit", "Word Limit", `Note stays within ${maxWords} words`],
    ["theme_count", "Theme Count", "At most 5 themes discovered; top 3 highlighted"],
    ["quote_count", "3 Quotes", "Exactly 3 user quotes in the note"],
    ["action_grounding", "3 Grounded Actions", "Exactly 3 actions, each tied to a theme"],
  ];
  const checks = eval_report?.checks;
  const checksPassed = checks ? CHECKS.filter(([k]) => checks[k]).length : 0;
  const allPassed = !!checks && checksPassed === CHECKS.length;

  const handleCopyDigest = () => {
    if (!pulse?.markdown) return;
    navigator.clipboard.writeText(pulse.markdown);
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
              {checks && (
                <span
                  className="badge-guardrails-pass"
                  style={allPassed ? undefined : { background: "#ffe4e6", color: "#e11d48" }}
                >
                  <Icon name={allPassed ? "check" : "warning"} sm /> Passed {checksPassed}/{CHECKS.length} guardrails
                </span>
              )}
              <span className="badge-published">
                <span className={`dot ${published ? "ok" : ""}`} style={{ width: 6, height: 6 }} />{" "}
                {published ? "Published" : pulse ? "Generated · not published" : "Scrubbed"}
              </span>
            </div>
          </div>
        </div>

        <div className="pulse-header-actions">
          <button
            className="btn ghost sm"
            onClick={handleCopyDigest}
            disabled={!pulse?.markdown}
            title={pulse?.markdown ? "Copy the pulse as markdown" : "Generate the pulse first"}
          >
            <Icon name={copiedDigest ? "done" : "content_copy"} sm />
            {copiedDigest ? "Copied!" : "Copy Digest"}
          </button>
          <button className="btn ghost sm" onClick={() => onSwitchTab?.("publish")}>
            <Icon name="cloud_upload" sm /> Publish
          </button>
          {docUrl && (
            <a href={docUrl} target="_blank" rel="noreferrer" className="btn ghost sm" style={{ color: "var(--accent-ink)" }}>
              <Icon name="open_in_new" sm /> Open Google Doc ↗
            </a>
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
          </div>
          <div className="pulse-metric-footer">
            <span>Play Store + App Store</span>
            <span className="text-emerald">PII-scrubbed</span>
          </div>
        </div>

        {/* Card 2: Blended Rating */}
        <div className="pulse-metric-card">
          <div className="pulse-metric-top">
            <span className="pulse-metric-label">Blended Rating</span>
          </div>
          <div className="pulse-metric-val">
            {avgRating != null ? avgRating.toFixed(2) : "—"} <span style={{ color: "#f59e0b", fontSize: 24 }}>★</span> <span style={{ fontSize: 18, color: "var(--muted)", fontWeight: 500 }}>/ 5.0</span>
          </div>
          <div className="pulse-multi-bar" role="img" aria-label="Rating breakdown">
            <span style={{ width: `${100 - lowRatingPct}%`, background: "#00b386" }} title={`3★–5★: ${100 - lowRatingPct}%`} />
            <span style={{ width: `${lowRatingPct}%`, background: "#f43f5e" }} title={`1★ & 2★: ${lowRatingPct}%`} />
          </div>
          <div className="pulse-metric-footer">
            <span>3★–5★ ({100 - lowRatingPct}%) · themed reviews</span>
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
            {wordCount} <span style={{ fontSize: 18, color: "var(--muted)", fontWeight: 500 }}>/ {maxWords} words</span>
          </div>
          <div className="pulse-multi-bar">
            <span style={{ width: isPending ? "0%" : `${densityPct}%`, background: "#00b386" }} />
          </div>
          <div className="pulse-metric-footer">
            <span>{isPending ? "Pending generation" : `${densityPct}% target density`}</span>
            <span className="text-emerald">{isPending ? `${maxWords} words budget` : `${wordsRemaining} words remaining`}</span>
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
                        <TrendBadge theme={t} />
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
                      <RatingMix dist={t.rating_dist} />
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
              <span className={published ? "badge-guardrails-pass" : "dist-item-badge gray"} style={{ fontSize: 10, padding: "2px 8px" }}>
                {published && <span className="dot ok" style={{ width: 5, height: 5 }} />}{" "}
                {published ? "Synced" : "Not published"}
              </span>
            </div>

            {/* Google Doc */}
            <div className="dist-item">
              <div className="dist-item-top">
                <span className="dist-item-name">
                  <Icon name="description" sm style={{ color: "#00b386" }} />
                  Google Doc · {pulseWeek}
                </span>
                <span className={`dist-item-badge ${docDone ? "green" : "gray"}`}>
                  {docDone ? "Appended ✓" : docFailed ? "Failed" : "Not appended"}
                </span>
              </div>
              {docSha && <div className="dist-item-detail">sha256: {docSha}</div>}
              {docUrl && (
                <a href={docUrl} target="_blank" rel="noreferrer" className="dist-item-link">
                  Open doc ↗
                </a>
              )}
            </div>

            {/* Gmail */}
            <div className="dist-item">
              <div className="dist-item-top">
                <span className="dist-item-name">
                  <Icon name="mail" sm style={{ color: "#3b82f6" }} />
                  Gmail Lead Digest
                </span>
                <span className={`dist-item-badge ${emailDone ? "green" : "gray"}`}>
                  {emailDone ? (emailMode === "send" ? "Sent ✓" : "Draft created ✓") : "Not drafted"}
                </span>
              </div>
              {settings?.recipient && (
                <div className="dist-item-detail" style={{ color: "var(--text-2)" }}>
                  TO: {settings.recipient}
                </div>
              )}
              <div className="dist-item-detail">
                SUBJ: Groww Weekly Review Pulse — {pulseWeek}
              </div>
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
              {checks ? (
                <span
                  className="badge-guardrails-pass"
                  style={{ fontSize: 10, padding: "2px 8px", ...(allPassed ? {} : { background: "#ffe4e6", color: "#e11d48" }) }}
                >
                  {checksPassed}/{CHECKS.length} Passed
                </span>
              ) : (
                <span className="dist-item-badge gray">Not run</span>
              )}
            </div>

            {CHECKS.map(([key, title, sub]) => {
              const ok = checks ? !!checks[key] : null;
              return (
                <div key={key} className="guardrail-item">
                  <Icon
                    name={ok === null ? "radio_button_unchecked" : ok ? "check_circle" : "cancel"}
                    className={ok ? "guardrail-icon-pass" : undefined}
                    sm
                    style={ok === false ? { color: "#e11d48" } : ok === null ? { color: "var(--muted)" } : undefined}
                  />
                  <div className="guardrail-item-content">
                    <span className="guardrail-item-title">{title}</span>
                    <span className="guardrail-item-sub">{sub}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}
