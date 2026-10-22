import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import Icon from "../components/Icon.jsx";
import { friendlyMessage } from "../friendly.js";

const STATUS_CHIP = { done: "ok", failed: "bad", unknown: "warn", pending: "" };
const when = (iso) => (iso ? new Date(iso).toLocaleString() : null);

export default function PublishTab({ week, data }) {
  const pulseWeek = data?.pulse_week || (week && week !== "all" ? week : null);
  const [settings, setSettings] = useState(null);
  const [pub, setPub] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(null); // "save" | "preview" | "publish"

  const [docInput, setDocInput] = useState("");
  const [newRcpt, setNewRcpt] = useState("");
  const [recipient, setRecipient] = useState("");
  const [docs, setDocs] = useState(true);
  const [email, setEmail] = useState("draft");
  const [forceAppend, setForceAppend] = useState(false);
  const [resend, setResend] = useState(false);
  const [confirmSend, setConfirmSend] = useState(false);
  const [preview, setPreview] = useState(null);

  const loadSettings = useCallback(async () => {
    try {
      const s = await api.publishSettings();
      setSettings(s);
      setDocInput((cur) => cur || s.doc_url || "");
      setRecipient((cur) => (cur && s.recipients.includes(cur) ? cur : s.recipient));
    } catch (e) { setError(e.message); }
  }, []);

  const loadState = useCallback(async () => {
    if (!week) return;
    try { setPub(await api.publishState(week)); } catch (e) { setError(e.message); }
  }, [week]);

  useEffect(() => { loadSettings(); }, [loadSettings]);
  useEffect(() => { setPreview(null); setNotice(null); loadState(); }, [loadState]);

  const state = pub?.state;
  const docDone = state?.doc_append?.status === "done";
  const emailDone = state?.email?.status === "done";
  const hasPulse = !!pub?.has_pulse;
  const live = !!settings?.mcp_token_set;

  const saveSettings = async (body, msg) => {
    setBusy("save"); setError(null); setNotice(null);
    try {
      const s = await api.savePublishSettings(body);
      setSettings(s);
      setRecipient((cur) => (body.recipient ? s.recipient : s.recipients.includes(cur) ? cur : s.recipient));
      if ("doc_id" in body) setDocInput(s.doc_url || "");
      setNotice(msg);
    } catch (e) { setError(e.message); }
    setBusy(null);
  };

  const addRecipient = async () => {
    if (!newRcpt.trim()) return;
    await saveSettings({ recipient: newRcpt.trim() }, `Added ${newRcpt.trim()} and set as active recipient.`);
    setNewRcpt("");
  };

  const run = async (dry) => {
    setBusy(dry ? "preview" : "publish"); setError(null); setNotice(null);
    try {
      const res = await api.publish(week, {
        docs, email, recipient: recipient || null,
        force_append: forceAppend, resend,
        confirm_send: email === "send" && !dry, dry_run: dry,
      });
      if (dry) {
        setPreview(res.message);
      } else if (res.status === "failed_publish" || res.status === "failed_validation") {
        setError(res.message);
      } else {
        const bits = [];
        if (docs) bits.push("Google Doc updated");
        if (email === "draft") bits.push(`Gmail draft created for ${recipient}`);
        if (email === "send") bits.push(`email sent to ${recipient}`);
        setNotice(`${bits.join(" · ")}.`);
        setForceAppend(false); setResend(false);
      }
      await Promise.all([loadState(), loadSettings()]);
    } catch (e) { setError(e.message); }
    setBusy(null); setConfirmSend(false);
  };

  const publishClicked = () => (email === "send" ? setConfirmSend(true) : run(false));

  if (!week) return <div className="empty">Select a week first.</div>;
  if (!settings || !pub) return <div className="empty">{error || "Loading…"}</div>;

  const nothing = !docs && email === "off";
  const emailBlocked = email !== "off" && !docs && !docDone;

  return (
    <>
      <div className="page-head">
        <div className="eyebrow">Delivery · Google Workspace via MCP</div>
        <h1>Publish</h1>
        <p>
          Append the weekly pulse to the shared Google Doc and email it to stakeholders.
          {" "}Publishing <b>{pub.week}</b>{week === "all" ? " (latest pulse from the all-reviews view)" : ""}.
        </p>
      </div>

      {error && <div className="banner bad" style={{ whiteSpace: "pre-wrap" }}>{friendlyMessage(error)}</div>}
      {notice && <div className="banner info">{notice}</div>}
      {!hasPulse && (
        <div className="banner warn">
          No pulse has been generated for {pub.week} yet. Use “Run pipeline” first, then come back to publish it.
        </div>
      )}
      {settings.problems.length > 0 && (
        <div className="banner warn">
          Publishing isn’t fully configured: {settings.problems.join(" · ")}
        </div>
      )}

      <div className="grid cols-2">
        {/* ------------------------------------------------ destinations */}
        <div className="card">
          <h3>Destinations</h3>

          <div className="field">
            <label htmlFor="doc">Google Doc</label>
            <div className="row">
              <input id="doc" type="text" style={{ flex: 1, minWidth: 180 }} value={docInput}
                placeholder={settings.auto_create_doc ? "Leave blank to auto-create on first publish" : "Doc URL or ID"}
                onChange={(e) => setDocInput(e.target.value)} />
              <button className="btn ghost" disabled={busy === "save"}
                onClick={() => saveSettings({ doc_id: docInput.trim() }, docInput.trim() ? "Document saved." : "Document override cleared.")}>
                Save
              </button>
            </div>
            <p className="help">
              {settings.doc_url
                ? <>Current: <a href={settings.doc_url} target="_blank" rel="noreferrer">open “{settings.doc_title}”</a></>
                : `No Doc yet — “${settings.doc_title}” is created on the first publish.`}
            </p>
          </div>

          <div className="field">
            <label>Email recipient</label>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {settings.recipients.map((r) => (
                <div key={r} className={`rcpt ${recipient === r ? "on" : ""}`} onClick={() => setRecipient(r)}>
                  <Icon name={recipient === r ? "radio_button_checked" : "radio_button_unchecked"} sm
                    style={{ color: recipient === r ? "var(--accent)" : "var(--muted)" }} />
                  <span className="addr">{r}</span>
                  {r === settings.recipient && <span className="chip ok">default</span>}
                  <button className="icon-btn" title="Remove recipient" aria-label={`Remove ${r}`}
                    onClick={(e) => { e.stopPropagation(); saveSettings({ remove_recipient: r }, `Removed ${r}.`); }}>
                    <Icon name="close" sm />
                  </button>
                </div>
              ))}
            </div>
            <div className="row" style={{ marginTop: 6 }}>
              <input type="email" style={{ flex: 1, minWidth: 180 }} placeholder="Add recipient, e.g. team@company.com"
                value={newRcpt} onChange={(e) => setNewRcpt(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addRecipient()} />
              <button className="btn ghost" disabled={busy === "save" || !newRcpt.trim()} onClick={addRecipient}>
                <Icon name="add" sm />Add
              </button>
            </div>
            <p className="help">One address per email (no cc/bcc). Pick who gets this one; adding makes it the default.</p>
          </div>
        </div>

        {/* ------------------------------------------------ actions */}
        <div className="card">
          <h3>Publish this pulse</h3>

          <label className="check" style={{ marginTop: 14 }}>
            <input type="checkbox" checked={docs} onChange={(e) => setDocs(e.target.checked)} />
            <span><b>Update Google Doc</b><br /><span className="help">Appends this week’s section to the document.</span></span>
          </label>
          {docs && docDone && (
            <label className="check" style={{ margin: "8px 0 0 26px" }}>
              <input type="checkbox" checked={forceAppend} onChange={(e) => setForceAppend(e.target.checked)} />
              <span className="help">Already appended — add a revised section anyway (needed if the content changed).</span>
            </label>
          )}

          <div className="field">
            <label>Email</label>
            <div className="seg" role="group" aria-label="Email mode">
              {[["off", "Don’t email"], ["draft", "Gmail draft"], ["send", "Send now"]].map(([v, l]) => (
                <button key={v} className={email === v ? "on" : ""} onClick={() => setEmail(v)}>{l}</button>
              ))}
            </div>
            {email !== "off" && (
              <p className="help">
                {email === "draft" ? "Creates a draft in Gmail — nothing is sent." : "Sends immediately."} To: <b>{recipient || "—"}</b>
              </p>
            )}
          </div>
          {email !== "off" && emailDone && (
            <label className="check" style={{ marginTop: 8 }}>
              <input type="checkbox" checked={resend} onChange={(e) => setResend(e.target.checked)} />
              <span className="help">Email already {state.email.mode === "send" ? "sent" : "drafted"} for this week — do it again (e.g. to a new recipient).</span>
            </label>
          )}
          {emailBlocked && <div className="banner warn" style={{ marginTop: 12, marginBottom: 0 }}>Email goes out only after the Doc has been updated — enable “Update Google Doc”.</div>}
          {!live && <div className="banner warn" style={{ marginTop: 12, marginBottom: 0 }}>MCP_AUTH_TOKEN isn’t set, so live publishing will fail. Preview still works.</div>}

          <div className="row end" style={{ marginTop: 20 }}>
            <button className="btn ghost" disabled={!hasPulse || nothing || !!busy} onClick={() => run(true)}>
              <Icon name="visibility" sm />{busy === "preview" ? "Rendering…" : "Preview"}
            </button>
            <button className="btn" disabled={!hasPulse || nothing || emailBlocked || !!busy} onClick={publishClicked}>
              <Icon name={email === "send" ? "send" : "cloud_upload"} sm />
              {busy === "publish" ? "Publishing…" : email === "send" ? "Publish & send" : "Publish"}
            </button>
          </div>
          {preview && <pre className="preview">{preview}</pre>}
        </div>
      </div>

      {/* ------------------------------------------------ status */}
      <h2>Delivery status · {pub.week}</h2>
      <div className="card">
        <div className="stepline">
          <Icon name="description" />
          <div className="grow">
            <b>Google Doc</b>
            <div className="help">
              {state?.doc_url ? <a href={state.doc_url} target="_blank" rel="noreferrer">Open document</a> : "Not published"}
              {state?.doc_append?.at && ` · ${when(state.doc_append.at)}`}
              {state?.doc_append?.error && ` · ${state.doc_append.error}`}
            </div>
          </div>
          <span className={`chip ${STATUS_CHIP[state?.doc_append?.status || "pending"]}`}>{state?.doc_append?.status || "pending"}</span>
        </div>
        <div className="stepline">
          <Icon name="mail" />
          <div className="grow">
            <b>Email</b>
            <div className="help">
              {state?.email?.mode ? `${state.email.mode === "send" ? "Sent" : "Drafted"}` : "Not sent"}
              {state?.email?.at && ` · ${when(state.email.at)}`}
              {state?.email?.error && ` · ${state.email.error}`}
            </div>
          </div>
          <span className={`chip ${STATUS_CHIP[state?.email?.status || "pending"]}`}>{state?.email?.status || "pending"}</span>
        </div>
      </div>

      {confirmSend && (
        <div className="modal-bg" onClick={() => setConfirmSend(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Confirm send">
            <h3 style={{ margin: 0 }}>Send this email now?</h3>
            <p style={{ color: "var(--text-2)" }}>
              The {pub.week} pulse will be emailed to <b>{recipient}</b>. This can’t be undone.
              {docs ? " The Google Doc is updated first." : ""}
            </p>
            <div className="row end">
              <button className="btn ghost" onClick={() => setConfirmSend(false)}>Cancel</button>
              <button className="btn" onClick={() => run(false)}>Send</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
