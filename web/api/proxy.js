// Vercel serverless proxy: browser -> /api/* (same origin) -> Railway API with a server-side token.
// The token (PULSE_API_TOKEN) and Railway URL (RAILWAY_API_URL) live only in Vercel env vars.
export default async function handler(req, res) {
  const base = (process.env.RAILWAY_API_URL || "").replace(/\/$/, "");
  if (!base) {
    res.status(500).json({ detail: "RAILWAY_API_URL is not set on Vercel" });
    return;
  }
  const path = [].concat(req.query.path || []).join("/");
  const qs = new URLSearchParams(req.query);
  qs.delete("path");
  const url = `${base}/api/${path}${qs.toString() ? `?${qs}` : ""}`;

  const headers = { "X-Pulse-Token": process.env.PULSE_API_TOKEN || "" };
  let body;
  if (req.method !== "GET" && req.method !== "HEAD") {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(req.body ?? {});
  }
  try {
    const r = await fetch(url, { method: req.method, headers, body });
    res.status(r.status);
    res.setHeader("Content-Type", r.headers.get("content-type") || "application/json");
    res.send(await r.text());
  } catch (e) {
    res.status(502).json({ detail: `Cannot reach the backend: ${e.message}` });
  }
}
