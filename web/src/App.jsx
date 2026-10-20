import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Icon from "./components/Icon.jsx";
import PulseTab from "./tabs/PulseTab.jsx";
import ThemesTab from "./tabs/ThemesTab.jsx";
import ReviewsTab from "./tabs/ReviewsTab.jsx";
import PublishTab from "./tabs/PublishTab.jsx";
import RunTab from "./tabs/RunTab.jsx";
import RunModal from "./components/RunModal.jsx";

const TABS = [
  ["pulse", "Pulse Digest", "insights", "Weekly digest"],
  ["themes", "Themes", "scatter_plot", "Theme analysis"],
  ["reviews", "Reviews", "rate_review", "Reviews explorer"],
  ["publish", "Publish", "send", "Google Doc & email"],
  ["run", "Runs", "account_tree", "Pipeline runs"],
];

const STATUS_POLL_MS = 15000;

function ago(iso) {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}

// Header status pill: what the pipeline is doing now, else how the last run ended.
function runPill(status) {
  const job = status?.job;
  if (job?.state === "running") {
    return { dot: "warn", text: `Running ${job.week || ""} · started ${ago(job.started_at)}` };
  }
  if (job?.state === "failed") return { dot: "bad", text: "Last run failed", title: job.error };
  const last = status?.last_run;
  if (!last) return { dot: "ok", text: "Idle · no runs yet" };
  const ok = last.status === "success";
  return {
    dot: ok ? "ok" : "bad",
    text: `Idle · Last run: ${last.week} ${ago(last.finished_at)}${ok ? "" : ` (${last.status})`}`,
    title: new Date(last.finished_at).toLocaleString(),
  };
}

function budgetPill(b) {
  if (!b) return null;
  const left = `${Math.round(b.tokens_remaining / 1000)}k`;
  const blocked = Boolean(b.blocked_until) || b.runs_used >= b.runs_max;
  const low = b.tokens_remaining < b.estimated_run_tokens;
  return {
    warn: blocked || low,
    text: `Runs ${b.runs_used}/${b.runs_max} · ${left} tokens left`,
    title:
      `Today (UTC): ${b.tokens_used.toLocaleString()} of ${b.tokens_allowance.toLocaleString()} tokens used, ` +
      `a run needs ~${b.estimated_run_tokens.toLocaleString()}.` +
      (b.blocked_until ? ` Groq quota exhausted until ${new Date(b.blocked_until).toLocaleString()}.` : ""),
  };
}

function initialTheme() {
  try {
    const saved = localStorage.getItem("pulse-theme");
    if (saved) return saved;
  } catch { /* storage unavailable */ }
  return "light"; // Stitch design default; dark is opt-in via the sidebar toggle
}

export default function App() {
  const [tab, setTab] = useState("pulse");
  const [weeks, setWeeks] = useState([]);
  const [week, setWeek] = useState(null);
  const [data, setData] = useState(null);
  const [health, setHealth] = useState(null);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [theme, setTheme] = useState(initialTheme);

  // Modal state
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [runModalWeek, setRunModalWeek] = useState("auto");
  const [runModalMode, setRunModalMode] = useState("auto");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("pulse-theme", theme); } catch { /* ignore */ }
  }, [theme]);

  const openRunModal = (targetWeek = "auto", mode = "auto") => {
    setRunModalWeek(targetWeek);
    setRunModalMode(mode);
    setRunModalOpen(true);
  };

  const loadWeeks = useCallback(async () => {
    try {
      const [w, h] = await Promise.all([api.weeks(), api.health()]);
      // Filter out any entries that don't have reviews or pulse
      const validWeeks = w.filter(item => item.has_pulse || (item.total_reviews && item.total_reviews > 0));
      setWeeks(validWeeks);
      setHealth(h);
      // Default to "all" (All Reviews Till Now) if available, or first valid week with pulse
      setWeek((cur) => cur || (validWeeks.find(x => x.week === "all") || validWeeks[0])?.week || null);
      setError(null);
    } catch (e) {
      setError(`Cannot reach the backend (${e.message}). Start it with: pulse serve`);
    }
  }, []);

  const loadWeek = useCallback(async (w) => {
    if (!w) return;
    setData(null);
    try {
      setData(await api.week(w));
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const loadStatus = useCallback(() => api.status().then(setStatus).catch(() => {}), []);

  useEffect(() => { loadWeeks(); }, [loadWeeks]);
  useEffect(() => { loadWeek(week); }, [week, loadWeek]);

  const refresh = () => { loadWeeks(); loadWeek(week); loadStatus(); };

  // Keep the header live, and reload the data when a run that was in progress finishes.
  const wasRunning = useRef(false);
  useEffect(() => {
    loadStatus();
    const id = setInterval(loadStatus, STATUS_POLL_MS);
    return () => clearInterval(id);
  }, [loadStatus]);
  useEffect(() => {
    const running = status?.job?.state === "running";
    if (wasRunning.current && !running) { loadWeeks(); loadWeek(week); }
    wasRunning.current = running;
  }, [status, week, loadWeeks, loadWeek]);

  const handleSelectChange = (e) => {
    const val = e.target.value;
    if (val === "__coming__") {
      openRunModal(health?.current_week || "2026-W43", "coming");
    } else if (val === "__custom__") {
      openRunModal(week === "all" ? "auto" : week, "custom");
    } else {
      setWeek(val);
    }
  };

  const current = TABS.find(([id]) => id === tab);
  const ok = (v) => (v ? "ok" : "bad");

  return (
    <>
      <aside className="sidebar">
        <div>
          <div className="brand">
            <div className="logo"><Icon name="monitoring" /></div>
            <div>
              <b>Groww Pulse</b>
              <small>Intelligence Platform</small>
            </div>
          </div>
          <div className="nav-label">Workspace</div>
          <nav className="nav">
            {TABS.map(([id, label, icon]) => (
              <button key={id} className={tab === id ? "on" : ""} onClick={() => setTab(id)} title={label}>
                <Icon name={icon} fill={tab === id} />
                <span>{label}</span>
              </button>
            ))}
          </nav>
        </div>
        <div className="side-foot">
          <div className="status-box">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h6>Pipeline status</h6>
              <span className="pulse-dot-ping">
                <span className="ping" />
                <span className="dot ok" />
              </span>
            </div>
            <div className="status-row"><i className={`dot ${ok(health?.llm_key_set)}`} />LLM Key: {health?.llm_key_set ? "Active (Groq OSS 120B)" : "Missing"}</div>
            <div className="status-row"><i className={`dot ${health?.mcp_token_set ? "ok" : "warn"}`} />MCP Server: {health?.mcp_token_set ? "Connected" : "Not Set"}</div>
            <div className="status-row"><i className={`dot ${error ? "bad" : "ok"}`} />API: {error ? "Unreachable" : "Connected"}</div>
            {health?.read_only && <div className="status-row"><i className="dot warn" />Read-only demo</div>}
          </div>
          <div className="side-tools">
            <span className="t" style={{ color: "var(--muted)", fontSize: 12, alignSelf: "center" }}>
              {theme === "dark" ? "Dark" : "Light"} mode
            </span>
            <button className="icon-btn" title="Toggle theme" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
              <Icon name={theme === "dark" ? "dark_mode" : "light_mode"} sm />
            </button>
          </div>
        </div>
      </aside>

      <div className="main-col">
        <header className="topbar">
          <div className="left">
            <span className="crumb hide-md">Weekly Digest</span>
            <Icon name="chevron_right" sm style={{ color: "var(--muted)" }} />
            <select className="week-select" value={week || ""} onChange={handleSelectChange} aria-label="Select week or view">
              {weeks.map((w) => (
                <option key={w.week} value={w.week}>
                  {w.display_label || (w.label ? `${w.label} · ${w.week}` : w.week)}
                </option>
              ))}
              <optgroup label="Generate & actions">
                <option value="__coming__">Generate pulse for coming week ({health?.current_week || "2026-W43"})…</option>
                <option value="__custom__">Generate for custom week / date…</option>
              </optgroup>
            </select>
            {(() => {
              const p = runPill(status);
              return (
                <div className="topbar-status-pill hide-md" title={p.title}>
                  <span className={`dot ${p.dot}`} /> {p.text}
                </div>
              );
            })()}
          </div>
          <div className="right">
            {(() => {
              const b = budgetPill(status?.budget);
              return b && (
                <div className={`topbar-budget-pill hide-md${b.warn ? " warn" : ""}`} title={b.title}>
                  <Icon name="data_usage" sm /> <span>{b.text}</span>
                </div>
              );
            })()}
            <button className="icon-btn" onClick={refresh} title="Refresh data" aria-label="Refresh data">
              <Icon name="refresh" sm />
            </button>
            <button className="btn" onClick={() => openRunModal(week === "all" ? "auto" : week, "auto")}>
              <Icon name="play_arrow" sm /> Run pipeline
            </button>
          </div>
        </header>

        <main className="content">
          {error && <div className="banner bad">{error}</div>}

          {tab === "run" ? (
            <RunTab
              week={week}
              weeks={weeks}
              data={data}
              health={health}
              onDone={refresh}
              onOpenModal={(w, mode) => openRunModal(w, mode)}
            />
          ) : tab === "publish" ? (
            <PublishTab week={week} data={data} health={health} />
          ) : !week ? (
            <div className="empty">No runs yet. Use “Run pipeline” to generate one.</div>
          ) : !data ? (
            <div className="empty">Loading…</div>
          ) : tab === "pulse" ? (
            <PulseTab
              data={data}
              onGenerate={(targetW) => openRunModal(targetW, "custom")}
              onSwitchWeek={(w) => setWeek(w)}
              onSwitchTab={(t) => setTab(t)}
            />
          ) : tab === "themes" ? (
            <ThemesTab
              data={data}
              onGenerate={(targetW) => openRunModal(targetW, "custom")}
            />
          ) : (
            <ReviewsTab week={week} themes={data.themes} summary={data.week_summary} />
          )}
        </main>
      </div>

      <nav className="mobile-nav">
        {TABS.map(([id, label, icon]) => (
          <button key={id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>
            <Icon name={icon} fill={tab === id} />{label.split(" ")[0]}
          </button>
        ))}
      </nav>

      <RunModal
        open={runModalOpen}
        onClose={() => setRunModalOpen(false)}
        initialWeek={runModalWeek}
        initialMode={runModalMode}
        weeks={weeks}
        health={health}
        onRunStarted={() => {
          refresh();
          setTab("run");
        }}
      />
    </>
  );
}
