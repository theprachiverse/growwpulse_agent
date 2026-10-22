import { useState, useEffect } from "react";
import { api } from "../api.js";
import { friendlyMessage } from "../friendly.js";

const STEPS = ["", "ingest", "preprocess", "theming", "compose", "publish"];

// 10 and 20 repeat the prompt overhead too often to fit the free daily budget; 100 can exceed
// Groq's 8k tokens-per-minute cap on a single call.
const BATCH_SIZES = [
  [25, "25 (Safer on rate limits)"],
  [50, "50 (Recommended)"],
  [75, "75 (Fewest tokens)"],
];

const k = (n) => `${Math.round(n / 1000)}k`;

// Convert YYYY-MM-DD to ISO 8601 week string (e.g. 2026-W43)
export function dateToIsoWeek(dStr) {
  if (!dStr) return "";
  const d = new Date(dStr + "T00:00:00Z");
  if (isNaN(d.getTime())) return "";
  d.setDate(d.getDate() + 4 - (d.getDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(weekNo).padStart(2, "0")}`;
}

export default function RunModal({
  open,
  onClose,
  initialWeek,
  initialMode = "auto",
  weeks = [],
  health,
  budget,
  onRunStarted,
}) {
  const currentW = health?.current_week || "2026-W43";
  const lastCompletedW = health?.last_completed_week || "2026-W42";

  const [mode, setMode] = useState(
    initialWeek === currentW ? "coming" : initialMode || "auto"
  );
  const [selectedWeek, setSelectedWeek] = useState(initialWeek || "auto");
  const [dateInput, setDateInput] = useState("");
  const [windowWeeks, setWindowWeeks] = useState(10);
  const [batchSize, setBatchSize] = useState(50);
  const [fromStep, setFromStep] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (initialWeek) {
      if (initialWeek === currentW) {
        setMode("coming");
        setSelectedWeek(currentW);
      } else if (initialWeek === lastCompletedW || initialWeek === "auto") {
        setMode("auto");
        setSelectedWeek("auto");
      } else if (initialWeek.startsWith("20")) {
        setMode("custom");
        setSelectedWeek(initialWeek);
      }
    }
  }, [initialWeek, currentW, lastCompletedW]);

  const handleDateChange = (e) => {
    const val = e.target.value;
    setDateInput(val);
    const computedWeek = dateToIsoWeek(val);
    if (computedWeek) {
      setSelectedWeek(computedWeek);
    }
  };

  const getEffectiveWeek = () => {
    if (mode === "coming") return currentW;
    if (mode === "auto") return "auto";
    return selectedWeek;
  };

  const start = async () => {
    setErr(null);
    setLoading(true);
    const targetWeek = getEffectiveWeek();
    try {
      const job = await api.startRun({
        week: targetWeek,
        window_weeks: Number(windowWeeks),
        dry_run: dryRun,
        from_step: fromStep || null,
        batch_size: batchSize || null,
      });
      setLoading(false);
      onRunStarted?.(job);
      onClose();
    } catch (e) {
      setLoading(false);
      setErr(e.message);
    }
  };

  if (!open) return null;

  const targetWeekDisplay = getEffectiveWeek();

  // Mirror the server's guardrail so the user hears about it before starting, not after a failed run.
  const runsExhausted = budget && budget.runs_used >= budget.runs_max;
  const quotaBlocked = budget && Boolean(budget.blocked_until);
  const blocked = runsExhausted || quotaBlocked;

  // Worst case (nothing cached): reviews in the chosen window, sent in batches of `batchSize`.
  const latest = weeks.find((w) => w.week === "all");
  const perWeek = latest?.total_reviews && latest?.window_weeks ? latest.total_reviews / latest.window_weeks : null;
  const cm = budget?.cost_model;
  const estReviews = perWeek ? Math.round(perWeek * windowWeeks) : null;
  // With a separate classify model only a capped sample (plus the capped residual pass) reaches the LLM,
  // and it is paid from that model's own ledger; the main ledger only covers discovery and compose.
  const cl = budget?.classify;
  const cap = (n, limit) => (limit > 0 ? Math.min(n, limit) : n);
  const llmReviews = estReviews && cl ? cap(estReviews, cl.max_reviews) + cl.max_residual_reviews : estReviews;
  const estCalls = llmReviews ? Math.ceil(llmReviews / batchSize) : null;
  const classifyTokens = llmReviews && cm ? llmReviews * cm.per_review + estCalls * cm.per_batch : null;
  const estTokens = classifyTokens != null ? cm.fixed + classifyTokens : null;
  const pacedTpm = cl?.tokens_per_minute || budget?.tokens_per_minute;
  const estMinutes = classifyTokens && pacedTpm ? Math.ceil(classifyTokens / pacedTpm) : null;
  const need = estTokens ?? budget?.estimated_run_tokens;
  const lowTokens =
    budget && !blocked &&
    (cl
      ? budget.tokens_remaining < Math.max(cm?.fixed ?? 0, budget.estimated_run_tokens) ||
        (classifyTokens != null && cl.tokens_remaining < classifyTokens)
      : budget.tokens_remaining < need);

  return (
    <div className="modal-bg" onClick={onClose}>
      <div
        className="modal"
        style={{ maxWidth: 480 }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Generate Pulse Pipeline"
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <strong style={{ fontSize: 17 }}>🚀 Generate Review Pulse</strong>
          <button
            className="btn ghost"
            style={{ padding: "4px 8px", fontSize: 13 }}
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 14 }}>
          According to <code>problemStatement.md</code>, the pipeline fetches public reviews from Google Play & Apple App Store for a rolling window of 8–12 weeks, scrubs PII, clusters into ≤5 themes, and generates a one-page executive pulse.
        </div>

        {err && <div className="banner bad" style={{ marginBottom: 12 }}>{friendlyMessage(err)}</div>}

        {blocked && (
          <div className="banner bad" style={{ marginBottom: 12 }}>
            {quotaBlocked
              ? `Groq's daily quota is exhausted until ${new Date(budget.blocked_until).toLocaleString()}.`
              : `Daily run limit reached (${budget.runs_used}/${budget.runs_max}).`}{" "}
            Classification progress is cached. Groq refills tokens continuously, so a later run resumes where the last one stopped.
          </div>
        )}
        {lowTokens && (
          <div className="banner warn" style={{ marginBottom: 12 }}>
            An uncached run needs ~{need.toLocaleString()} tokens but only{" "}
            {(cl ? Math.min(budget.tokens_remaining, cl.tokens_remaining) : budget.tokens_remaining).toLocaleString()}{" "}
            are left today on one of the models. The run will classify what it can, save its
            progress, and finish on the next run. A shorter window or bigger batches lower the cost.
          </div>
        )}

        <label style={{ fontWeight: 600 }}>Target Week / Date</label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 10 }}>
          <button
            type="button"
            className={`btn ${mode === "coming" ? "" : "ghost"}`}
            style={{ fontSize: 13, padding: "8px 10px", textAlign: "left" }}
            onClick={() => { setMode("coming"); setSelectedWeek(currentW); }}
          >
            <div><strong>Coming Week</strong></div>
            <div style={{ fontSize: 12, opacity: 0.85 }}>{currentW} (In-progress)</div>
          </button>

          <button
            type="button"
            className={`btn ${mode === "auto" ? "" : "ghost"}`}
            style={{ fontSize: 13, padding: "8px 10px", textAlign: "left" }}
            onClick={() => { setMode("auto"); setSelectedWeek("auto"); }}
          >
            <div><strong>Latest Finished</strong></div>
            <div style={{ fontSize: 12, opacity: 0.85 }}>{lastCompletedW} (Completed)</div>
          </button>
        </div>

        <div style={{ marginBottom: 12 }}>
          <button
            type="button"
            className={`btn ${mode === "custom" ? "" : "ghost"}`}
            style={{ width: "100%", fontSize: 13, padding: "7px 10px", textAlign: "left", marginBottom: mode === "custom" ? 8 : 0 }}
            onClick={() => setMode("custom")}
          >
            📅 Pick a Custom Date or Select Week...
          </button>

          {mode === "custom" && (
            <div style={{ background: "var(--bg)", padding: 12, borderRadius: 8, marginTop: 8, border: "1px solid var(--line)" }}>
              <div style={{ marginBottom: 8 }}>
                <label style={{ margin: "0 0 4px", fontSize: 12 }}>Pick Any Date on Calendar</label>
                <input
                  type="date"
                  value={dateInput}
                  onChange={handleDateChange}
                  style={{ width: "100%", padding: "7px 10px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--surface)" }}
                />
              </div>

              <div>
                <label style={{ margin: "0 0 4px", fontSize: 12 }}>Or Select from Existing Weeks with Reviews</label>
                <select
                  value={selectedWeek}
                  onChange={(e) => setSelectedWeek(e.target.value)}
                  style={{ width: "100%" }}
                >
                  <option value={currentW}>{currentW} (Coming Week)</option>
                  {weeks?.filter((w) => w.week !== "all").map((w) => (
                    <option key={w.week} value={w.week}>
                      {w.display_label || (w.label ? `${w.label} · ${w.week}` : w.week)}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}
        </div>

        <div style={{ background: "var(--accent-soft)", padding: "8px 12px", borderRadius: 6, fontSize: 13, marginBottom: 12 }}>
          Target ISO Week: <strong>{targetWeekDisplay}</strong>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <div>
            <label style={{ fontSize: 12 }}>Rolling Window (8–12 wks)</label>
            <select value={windowWeeks} onChange={(e) => setWindowWeeks(Number(e.target.value))}>
              <option value={8}>8 weeks</option>
              <option value={10}>10 weeks (Standard)</option>
              <option value={12}>12 weeks</option>
            </select>
          </div>

          <div>
            <label style={{ fontSize: 12 }}>Classification Batch Size</label>
            <select value={batchSize} onChange={(e) => setBatchSize(Number(e.target.value))}>
              {BATCH_SIZES.map(([n, label]) => (
                <option key={n} value={n}>{label}</option>
              ))}
            </select>
          </div>
        </div>

        {budget && (
          <div
            className={`banner ${estTokens && !lowTokens ? "info" : "warn"}`}
            style={{ marginTop: 10, marginBottom: 0, fontSize: 12 }}
            title="Worst case: assumes no review was classified before. Reviews already in the cache cost nothing."
          >
            {estTokens ? (
              <>
                <strong>~{k(estTokens)} tokens</strong> · {estCalls} LLM calls · ~{estMinutes} min at Groq's free rate
                limit
                <br />
                {k(budget.tokens_remaining)} of {k(budget.tokens_allowance)} left today
                {cl && <> (classify model: {k(cl.tokens_remaining)} of {k(cl.tokens_allowance)})</>} · runs{" "}
                {budget.runs_used}/{budget.runs_max}
              </>
            ) : (
              <>
                {k(budget.tokens_remaining)} of {k(budget.tokens_allowance)} tokens left today · runs{" "}
                {budget.runs_used}/{budget.runs_max}. A first full run uses about {k(budget.estimated_run_tokens)}.
              </>
            )}
          </div>
        )}

        <label style={{ fontSize: 12, marginTop: 10 }}>Resume from step (optional)</label>
        <select value={fromStep} onChange={(e) => setFromStep(e.target.value)}>
          {STEPS.map((s) => (
            <option key={s} value={s}>{s ? `Step: ${s}` : "Full Pipeline (Ingest → Preprocess → Theming → Compose → Publish)"}</option>
          ))}
        </select>

        <label className="check" style={{ marginTop: 12 }}>
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
          />
          <span>Dry run (generates pulse without updating Google Doc or Gmail)</span>
        </label>

        {!dryRun && (
          <div className="banner warn" style={{ marginTop: 8, fontSize: 12, padding: "8px 10px" }}>
            Live run will create/update Google Doc and create a Gmail draft via MCP.
          </div>
        )}

        <div className="row" style={{ marginTop: 16 }}>
          <button className="btn ghost" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          <button className="btn" onClick={start} disabled={loading || blocked}>
            {loading ? "Starting…" : `Start Pipeline for ${targetWeekDisplay}`}
          </button>
        </div>
      </div>
    </div>
  );
}
