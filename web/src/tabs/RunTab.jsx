import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import RunModal from "../components/RunModal.jsx";

export default function RunTab({ week, weeks = [], data, health, onDone, onOpenModal }) {
  const [job, setJob] = useState({ state: "idle" });
  const [open, setOpen] = useState(false);
  const prev = useRef("idle");

  useEffect(() => {
    let live = true;
    const tick = () => api.runStatus().then((j) => live && setJob(j)).catch(() => {});
    tick();
    const t = setInterval(tick, 3000);
    return () => { live = false; clearInterval(t); };
  }, []);

  useEffect(() => {
    if (prev.current === "running" && job.state !== "running") onDone();
    prev.current = job.state;
  }, [job.state]); // eslint-disable-line react-hooks/exhaustive-deps

  const m = data?.manifest;
  const ev = data?.eval_report;
  const running = job.state === "running";
  const checks = m?.eval_summary && Object.keys(m.eval_summary).length ? m.eval_summary : ev?.checks || ev;

  const handleOpenModal = () => {
    if (onOpenModal) {
      onOpenModal(week || "auto", "auto");
    } else {
      setOpen(true);
    }
  };

  return (
    <>
      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <div>
            <strong>Pipeline run</strong>
            <div style={{ color: "var(--muted)", fontSize: 13 }}>
              LLM key {health?.llm_key_set ? "set" : "missing"} · MCP token {health?.mcp_token_set ? "set" : "missing (publishing disabled)"}
            </div>
          </div>
          <button className="btn" disabled={running} onClick={handleOpenModal}>{running ? "Running…" : "New run"}</button>
        </div>
        {running && (
          <div className="banner info" style={{ marginTop: 12 }}>
            Running {job.week} ({job.dry_run ? "dry run" : "live publish"}) since {new Date(job.started_at).toLocaleTimeString()}.
            Theming makes about 30 LLM calls and can take 10+ minutes.
          </div>
        )}
        {job.state === "done" && (
          <div className="banner info" style={{ marginTop: 12 }}>Last run finished for {job.week}: <strong>{job.status}</strong>.</div>
        )}
        {job.state === "failed" && <div className="banner bad" style={{ marginTop: 12 }}>Last run failed: {job.error}</div>}
      </div>

      {!data ? (
        <div className="empty">Select a week.</div>
      ) : (
        <>
          <h2>Run manifest · {week}</h2>
          {!m ? (
            <div className="card">No manifest for this week (only a full run writes one).</div>
          ) : (
            <div className="card">
              <dl className="kv">
                <dt>Status</dt>
                <dd><span className={`chip ${m.status === "success" ? "ok" : "bad"}`}>{m.status}</span></dd>
                <dt>Started</dt><dd>{new Date(m.started_at).toLocaleString()}</dd>
                <dt>Counts</dt>
                <dd>{Object.entries(m.counts || {}).map(([k, v]) => `${k}: ${v}`).join(" · ") || "–"}</dd>
                <dt>Source errors</dt>
                <dd>{Object.keys(m.source_errors || {}).length ? JSON.stringify(m.source_errors) : "none"}</dd>
                <dt>LLM usage</dt>
                <dd>{Object.entries(m.llm_usage || {}).map(([k, v]) => `${k}: ${v}`).join(" · ") || "–"}</dd>
                <dt>Google Doc</dt>
                <dd>{m.doc_url ? <a href={m.doc_url} target="_blank" rel="noreferrer">Open doc</a> : "not published"}</dd>
              </dl>
            </div>
          )}

          <h2>Guardrail checks</h2>
          <div className="card">
            {checks && Object.keys(checks).length ? (
              <dl className="kv">
                {Object.entries(checks).map(([k, v]) => (
                  <Fragment key={k}>
                    <dt>{k}</dt>
                    <dd>
                      {typeof v === "boolean" ? (
                        <span className={`chip ${v ? "ok" : "bad"}`}>{v ? "pass" : "fail"}</span>
                      ) : (
                        JSON.stringify(v)
                      )}
                    </dd>
                  </Fragment>
                ))}
              </dl>
            ) : (
              "No eval report for this week."
            )}
          </div>
        </>
      )}

      {open && (
        <RunModal
          open={open}
          onClose={() => setOpen(false)}
          initialWeek={week}
          weeks={weeks}
          health={health}
          onRunStarted={(j) => {
            setJob(j);
            onDone?.();
          }}
        />
      )}
    </>
  );
}
