# Deployment Plan — Groww Review Pulse: Vercel (frontend) + Railway (backend)

Status: **code-complete, not yet deployed.** No Streamlit.

```
Browser ──► Vercel: static React app (web/dist)
                │  /api/* → api/proxy.js (adds X-Pulse-Token)
                ▼
            Railway: FastAPI (pulse.api via uvicorn) ── Volume /data
                ├─ pipeline runs + publish (Doc / Gmail via MCP server)
                ├─ weekly scheduler process (optional)
                └─ Groq LLM
```

## 1. Files

| File | Role |
|---|---|
| `vercel.json` | Installs/builds `web/`, serves `web/dist`. |
| `api/proxy.js` | Vercel function: forwards `/api/*` to Railway with the secret token. |
| `Dockerfile`, `railway.json` | Backend image (`uv sync --extra web`), healthcheck `/api/health`. |
| `scripts/railway_start.sh` | Seeds empty `/data/runs` from bundled `deploy_data/runs`, starts the scheduler if enabled, runs uvicorn. |
| `src/pulse/scheduler.py` | Monday 03:30 UTC check; runs last completed week if no success yet. |
| `src/pulse/api.py` | `PULSE_API_TOKEN` gate on `/api/*` (health exempt). |

## 2. Railway (backend) — deploy first

1. New Project → Deploy from GitHub repo (Dockerfile detected). Root directory: repo root.
2. Add a **Volume** mounted at `/data`. Replicas = 1.
3. Networking → **Generate domain** (Vercel needs a URL to call), e.g. `https://xxx.up.railway.app`.
4. Variables:

| Variable | Value |
|---|---|
| `LLM_PROVIDER` | `groq` |
| `LLM_API_KEY` | Groq key |
| `MCP_AUTH_TOKEN` | MCP bearer token (≥32 chars) |
| `MCP_SERVER_URL` | `https://mcpserver-production-da04.up.railway.app/mcp` |
| `PULSE_RECIPIENT` | default recipient |
| `PULSE_DATA_DIR` | `/data` |
| `PULSE_API_TOKEN` | shared secret for the Vercel proxy (see §3) |
| `PULSE_SCHEDULE_ENABLED` | `1` to enable the weekly scheduler (optional) |
| `PULSE_SCHEDULE_LIVE` | `1` to publish on schedule; unset = dry runs (optional) |
| `PULSE_DOC_ID`, `PULSE_LOG_LEVEL` | optional |

5. Check `https://xxx.up.railway.app/api/health` → `{"ok": true, ...}`.

## 3. Vercel (frontend + token proxy)

The browser only talks to Vercel (`/api/*`). `api/proxy.js` forwards each call to Railway and adds the secret `X-Pulse-Token`, so the token never reaches the browser and Railway rejects anyone without it.

1. Generate a long random token, e.g. `python -c "import secrets;print(secrets.token_urlsafe(32))"`.
2. **Railway** variable: `PULSE_API_TOKEN` = that token. (Railway still needs a generated domain so Vercel can reach it, but every `/api` route except `/api/health` returns 401 without the token.)
3. **Vercel**: import the repo, Root Directory empty, Framework Preset **Other**, overrides off. Environment variables:
   - `RAILWAY_API_URL` = `https://xxx.up.railway.app` (no trailing slash)
   - `PULSE_API_TOKEN` = the same token
4. Deploy, then turn on **Deployment Protection** (Vercel Authentication) so only you can open the site.

`PULSE_CORS_ORIGINS` / `VITE_API_BASE` are not needed in this setup.

## 3a. Deploying a code change (what updates and what does not)

- **Frontend:** Vercel rebuilds `web/` from source on every push (`npm run build`; `web/dist` is not committed), so the live UI always matches the pushed code. Hard-refresh once if a tab was open during the deploy.
- **Backend:** Railway rebuilds the image from the repo, including `config/config.yaml`, so config changes (models, caps, run limits) ship with the push. No new environment variables are needed.
- **Data is not touched:** the `/data` volume is seeded once from `deploy_data/runs` and never overwritten, so existing pulses and run history stay. Ledger files (`llm_budget.json`, `llm_budget_classify.json`) live on the volume; an old-format ledger is migrated automatically on first read.
- **Order does not matter:** the UI treats the newer `classify` budget field as optional, and an older UI ignores it. The budget pill is hidden on a read-only public deployment either way.
- **One Groq quota, two ledgers:** a local machine and Railway using the same Groq key share one rolling quota per model, but each keeps its own ledger. Neither sees the other's usage until Groq refuses a call, at which point the ledger adopts Groq's reported usage. Avoid running both close together, and keep the scheduler (`PULSE_SCHEDULE_ENABLED`) in mind when testing locally.

## 4. Security

- Railway's API is token-gated (`PULSE_API_TOKEN`); only `/api/health` is open. Do not put the token in any `VITE_*` variable (it would ship in the JS bundle).
- The site itself has no login; use Vercel Deployment Protection. Use publish "draft"; avoid Send now.
- Rotate the token (both places) if it leaks.

## 5. Data

`data/` is git-ignored. The image bundles `deploy_data/runs` (PII-scrubbed) and seeds the Volume on first boot (never overwrites). `publish_state.json` is not bundled: a week already published from your laptop looks unpublished on Railway — copy `data/state` and that week's `publish_state.json` to `/data` before publishing, or you may duplicate the Doc section/email. Refresh the bundle with `python scripts/prepare_vercel_data.py` (copies `data/runs` → `deploy_data/runs`).

## 6. Verification

- [ ] Railway `/api/health` OK; logs show "Seeding /data from deploy_data/" on first boot.
- [ ] Vercel site loads; Pulse/Themes/Reviews show data (no CORS errors in the browser console).
- [ ] Publish → Preview works; a dry-run pipeline starts and finishes.
- [ ] Add a recipient, redeploy Railway → still there (Volume works).
- [ ] If scheduler enabled: logs show `scheduler_started`.

## 7. Caveats

- Redeploying Railway during a run kills it (resume with `--from-step`).
- Single replica; Volume is per-instance.
- Local dev: `uvicorn pulse.api:app` + `cd web && npm run dev`.
