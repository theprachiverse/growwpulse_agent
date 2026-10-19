import { useCallback, useEffect, useState } from "react";
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

  useEffect(() => { loadWeeks(); }, [loadWeeks]);
  useEffect(() => { loadWeek(week); }, [week, loadWeek]);

  const refresh = () => { loadWeeks(); loadWeek(week); };

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
            <div className="status-row"><i className={`dot ${error ? "bad" : "ok"}`} />DB Sync: {error ? "Unreachable" : "Synced 4m ago"}</div>
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
                  {w.week === "all" ? "" : w.has_pulse ? " (has pulse)" : w.total_reviews ? ` (${w.total_reviews} reviews)` : ""}
                </option>
              ))}
              <optgroup label="Generate & actions">
                <option value="__coming__">Generate pulse for coming week ({health?.current_week || "2026-W43"})…</option>
                <option value="__custom__">Generate for custom week / date…</option>
              </optgroup>
            </select>
            <div className="topbar-status-pill hide-md">
              <span className="dot ok" /> Pipeline idle · Last run: 18 Oct 04:30 IST
            </div>
          </div>
          <div className="right">
            <div className="topbar-tokens-pill hide-md">
              <Icon name="verified_user" sm style={{ color: "#00b386" }} />
              <span>Tokens Configured</span>
            </div>
            <button className="btn ghost hide-md" onClick={() => setTab("reviews")}>
              <Icon name="search" sm />
              <span>Quick search</span>
              <kbd>⌘K</kbd>
            </button>
            <button className="btn ghost hide-md" onClick={refresh} title="Refresh data">
              <Icon name="refresh" sm /> Refresh
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
