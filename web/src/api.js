// Empty in local dev (Vite proxies /api); on Vercel set VITE_API_BASE to the Railway API URL.
let BASE = (import.meta.env.VITE_API_BASE || "").trim();
if (BASE && !BASE.startsWith("http://") && !BASE.startsWith("https://")) {
  BASE = `https://${BASE}`;
}
BASE = BASE.replace(/\/+$/, "").replace(/\/api$/, "");

async function req(path, opts) {
  const r = await fetch(BASE + path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* keep statusText */ }
    throw new Error(msg);
  }
  return r.json();
}

const post = (path, body) =>
  req(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  health: () => req("/api/health"),
  weeks: () => req("/api/weeks"),
  week: (w) => req(`/api/weeks/${w}`),
  reviews: (w, params) => req(`/api/weeks/${w}/reviews?${new URLSearchParams(params)}`),
  runStatus: () => req("/api/run"),
  publishSettings: () => req("/api/publish/settings"),
  savePublishSettings: (body) => post("/api/publish/settings", body),
  publishState: (w) => req(`/api/weeks/${w}/publish`),
  publish: (w, body) => post(`/api/weeks/${w}/publish`, body),
  startRun: (body) =>
    req("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
};

export const SOURCE = { play_store: "Play Store", app_store: "App Store" };
export const pct = (x, d = 1) => (x == null ? "–" : `${(x * 100).toFixed(d)}%`);
