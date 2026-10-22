# Groww Weekly Review Pulse

An AI workflow that turns public **Groww** app reviews (Google Play Store + Apple App Store) into a **one-page weekly pulse** a product team can read in a few minutes:

- **Top 3 themes**: what users care about most, ranked.
- **3 verbatim quotes**: what they actually said, copied exactly from real reviews.
- **3 action ideas**: what to do next, each tied to a theme.

The pulse is 250 words or fewer and contains no personal data. It is written to a **Google Doc** and a **Gmail draft** through an **MCP (Model Context Protocol) server**, so this repo never handles Google credentials.

Target product: Groww (Stocks, Mutual Funds, IPO). Play package `com.nextbillion.groww`, App Store id `1404871703` (India storefront).

---

## Contents

1. [How it works](#1-how-it-works)
2. [Tech stack](#2-tech-stack)
3. [Project structure](#3-project-structure)
4. [Quick start](#4-quick-start)
5. [Configuration](#5-configuration)
6. [CLI reference](#6-cli-reference)
7. [Web app and API](#7-web-app-and-api)
8. [LLM budget (Groq free tier)](#8-llm-budget-groq-free-tier)
9. [Theming in detail](#9-theming-in-detail)
10. [Guardrails and evals](#10-guardrails-and-evals)
11. [Privacy and security](#11-privacy-and-security)
12. [Scheduling](#12-scheduling)
13. [Deployment](#13-deployment)
14. [Testing and code quality](#14-testing-and-code-quality)
15. [Known limitations](#15-known-limitations)
16. [Further reading](#16-further-reading)

---

## 1. How it works

```
 Play Store ─┐                                                    ┌─► Google Doc (append)
             ├─► ingest ─► preprocess ─► theming ─► compose ─► guardrails ─► publish ─┤
 App Store ──┘   (fetch)   (clean, PII)  (themes)   (pulse)    (validate)    (MCP)    └─► Gmail draft
```

| Step | Module | What it does |
|---|---|---|
| **Ingest** | `src/pulse/ingest/` | Fetches reviews from both stores for a rolling window (default 10 weeks). Play uses `google-play-scraper`; the App Store RSS feed only reaches about 500 recent reviews, so weekly snapshots are accumulated to build the full window. A source that fails is recorded and the run continues with the other. |
| **Preprocess** | `src/pulse/preprocess/` | Drops short reviews (8 words or fewer) and non-English ones, strips emojis, scrubs PII (emails, phones, IDs, and so on), and replaces review IDs with pseudonyms. Raw reviews are never saved. |
| **Theming** | `src/pulse/theming/` | Clusters reviews, has the LLM name up to 5 product themes, labels every review with one theme or `other`, then ranks the themes. See [section 9](#9-theming-in-detail). |
| **Compose** | `src/pulse/compose/` | Picks 3 verbatim quotes and writes 3 actions with the LLM, renders the one-page pulse, and shortens it to 250 words or fewer without ever editing the quotes. |
| **Guardrails** | `src/pulse/guardrails/` | Hard checks that block publishing (quote is an exact substring, no PII, word limit, 3 actions on Top-3 themes, and more). |
| **Publish** | `src/pulse/publish/` | Appends the pulse to the Google Doc and creates a Gmail draft (or sends, only if explicitly asked) through MCP. Idempotent per week. |

A **run manifest** (`data/runs/<week>/run_manifest.json`) is the audit record for every run: status, timings, counts, token usage and Doc/email outcome.

---

## 2. Tech stack

**Backend (Python 3.11+)**

| Area | Tools |
|---|---|
| Language / packaging | Python 3.11+, `uv` (lockfile `uv.lock`), `hatchling` |
| CLI | Typer (`pulse` command) |
| API | FastAPI + Uvicorn |
| Data / config | Pydantic v2, pydantic-settings, PyYAML, Jinja2 |
| HTTP / logging | httpx, structlog |
| Review sources | `google-play-scraper`, App Store RSS |
| Text processing | scikit-learn (TF-IDF + truncated SVD), SciPy, langdetect |
| LLM | Groq (OpenAI-compatible API): `openai/gpt-oss-120b` and `openai/gpt-oss-20b` |
| Publishing | `mcp` Python SDK (streamable HTTP) to a separate Google Workspace MCP server |

**Frontend (`web/`)**: React 18 + Vite 5.

**Quality**: pytest (+ asyncio, cov), respx, ruff, mypy, pre-commit.

**Hosting**: Docker + Railway (backend, with a persistent volume) and Vercel (static frontend + a token-adding proxy).

---

## 3. Project structure

```
.
├── config/
│   ├── config.yaml            # behaviour: sources, window, themes, LLM models, budget, publish
│   └── mcp_servers.yaml       # MCP server URL, tool map and tool allow-list
├── src/pulse/
│   ├── cli.py                 # `pulse` command (fetch, preprocess, theme, compose, run, publish, eval, serve, budget)
│   ├── orchestrator.py        # runs the steps in order, writes the manifest
│   ├── api.py                 # FastAPI app used by the web UI
│   ├── scheduler.py           # weekly loop for the always-on container
│   ├── ingest/ preprocess/ theming/ compose/ guardrails/ publish/
│   ├── llm/                   # Groq client, local embedder, token budget, prompts
│   └── run_store.py           # on-disk artifacts per week
├── web/                       # React + Vite frontend (tabs: Pulse, Themes, Reviews, Publish, Runs)
│   └── api/proxy.js           # Vercel function: forwards /api/* to Railway with the secret token
├── evals/                     # offline eval suite, fixtures, golden labels, fake MCP server
├── tests/                     # unit tests (about 250)
├── scripts/                   # Railway entrypoint, Windows scheduler scripts, fixture builders
├── deploy_data/               # PII-scrubbed sample runs that seed the Railway volume on first boot
├── Docs/                      # problem statement, architecture, edge cases, evals, deployment plan
├── Dockerfile  railway.json   # backend image and Railway config
└── pyproject.toml  uv.lock
```

Data written at run time lives under `data/` (git-ignored): `runs/<week>/` (cleaned reviews, assignments, themes, `pulse.md`, `pulse.json`, eval report, manifest), `cache/` (LLM label cache), `snapshots/`, `state/`, `logs/`, and the two token ledgers.

---

## 4. Quick start

**Prerequisites:** Python 3.11+, [`uv`](https://docs.astral.sh/uv/), a free [Groq](https://console.groq.com) API key, and (for publishing) an MCP server URL and bearer token. Node 20+ is only needed for the web UI.

```powershell
# 1. Install
uv sync --extra web

# 2. Configure secrets
copy .env.example .env          # then fill in LLM_API_KEY and MCP_AUTH_TOKEN

# 3. Full pipeline, nothing published
.venv\Scripts\pulse.exe run --dry-run --skip-health-check

# 4. See what's left of today's token budget
.venv\Scripts\pulse.exe budget

# 5. Tests
.venv\Scripts\python -m pytest -q
```

**Web UI locally** (two terminals):

```powershell
.venv\Scripts\pulse.exe serve                  # API on http://127.0.0.1:8000
cd web ; npm install ; npm run dev             # UI on http://127.0.0.1:5173 (proxies /api to :8000)
```

A dry run saves the themes, the pulse and the eval report. It only skips the Doc and email.

---

## 5. Configuration

Behaviour is in `config/config.yaml`. Secrets and per-environment values are in `.env`.

**Environment variables** (see `.env.example`)

| Variable | Purpose |
|---|---|
| `LLM_API_KEY` | Groq API key. `LLM_PROVIDER=groq`. |
| `MCP_AUTH_TOKEN` | Bearer token for the MCP server (at least 32 characters). |
| `MCP_SERVER_URL` | Optional override of the URL in `config/mcp_servers.yaml`. |
| `PULSE_RECIPIENT` | Address that receives the pulse email (one plain address). |
| `PULSE_DOC_ID` | Optional: reuse an existing Google Doc. Blank means the first publish creates one and remembers its ID. |
| `PULSE_DATA_DIR` | Where runs, cache and ledgers are stored (default `data`). |
| `PULSE_API_TOKEN` | Shared secret the Vercel proxy sends; the API rejects other callers (except `/api/health`). |
| `PULSE_READ_ONLY`, `PULSE_CORS_ORIGINS`, `PULSE_LOG_LEVEL` | API read-only mode, allowed origins, log level. |
| `PULSE_SCHEDULE_ENABLED`, `PULSE_SCHEDULE_LIVE`, `PULSE_SCHEDULE_HOUR_UTC` | Weekly scheduler on/off, live publishing vs dry runs, start hour (default 3.5 UTC = 09:00 IST). |

**Main `config.yaml` sections**

| Section | Controls |
|---|---|
| `sources` | Play Store / App Store ids, country, language, max reviews, App Store weekly accumulation |
| `ingest` | window (8-12 weeks), minimum reviews, minimum words (more than 8), English only, emoji stripping |
| `privacy` | optional Presidio name scrubbing |
| `ranking` | `volume` or `balanced` (balances Play vs App Store share) |
| `theming` | max themes (hard cap 5), top 3, classification batch size, seed taxonomy, residual pass |
| `llm` | chat model, classify model, temperatures, reasoning effort, token budget |
| `compose` | max 250 words, target 220, quote length 8-40 words |
| `publish` | `email_mode` (`draft` default, `send`, `off`), HTML or text email, Doc title |

---

## 6. CLI reference

All commands are subcommands of `pulse`.

| Command | What it does |
|---|---|
| `pulse run` | Full pipeline. Options: `--week 2026-W42` or `auto` (last completed week), `--dry-run`, `--from-step ingest\|preprocess\|theming\|compose\|publish`, `--send`, `--yes`, `--force-append`, `--skip-health-check`. |
| `pulse fetch` / `preprocess` / `theme` / `compose` | Run one stage on its own. `theme` and `compose` accept `--dry-run`. |
| `pulse publish` | Publish a saved pulse: `--week`, `--docs-only`, `--email-only`, `--send`, `--resend`, `--force-append`, `--doc-confirmed`. |
| `pulse eval` | Offline eval suite on a week's recorded artifacts (free). `--suite all\|compliance\|quality\|publish`, `--live` (re-runs theming twice, costs tokens), `--judge` (LLM-as-judge, costs tokens), `--no-report`. |
| `pulse budget` | Runs and tokens left for each Groq model, and when the next run fits. |
| `pulse serve` | Starts the FastAPI app (`--host`, `--port`). |
| `pulse config` | Prints the effective configuration. |

Sending email needs `--send` and a confirmation (`--yes` skips it). The default is always a draft.

---

## 7. Web app and API

The React UI (`web/`) reads everything from the API and has five tabs:

| Tab | Shows |
|---|---|
| **Pulse** | The weekly digest: top themes, quotes, actions, guardrail results |
| **Themes** | Share-of-reviews bar, theme breakdown (reviews, share, trend, average rating, star mix, low-rating share, per-store counts) and a warning when `other` is above 20% |
| **Reviews** | Explorer with text search, rating and theme filters, and sorting |
| **Publish** | Doc and email status, draft vs send settings, publish actions |
| **Runs** | Run manifest (status, counts, token usage, Doc link) and guardrail checks per week |

The top bar shows the pipeline state, how many runs and tokens are left, and the **Run pipeline** button.

**API endpoints** (`src/pulse/api.py`)

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Liveness and config flags (open, no token needed) |
| `GET /api/weeks`, `GET /api/weeks/{week}` | Available weeks and the full data for one week (`all` is accepted) |
| `GET /api/weeks/{week}/reviews` | Cleaned reviews with their theme |
| `GET /api/status`, `GET /api/run`, `POST /api/run` | Job state, budget, and starting a run (returns 202) |
| `GET/POST /api/publish/settings` | Draft vs send, recipient settings |
| `GET/POST /api/weeks/{week}/publish` | Publish state and publishing a week |

Every route except `/api/health` requires the `X-Pulse-Token` header when `PULSE_API_TOKEN` is set.

---

## 8. LLM budget (Groq free tier)

Groq's free tier gives about 200k tokens per day per model (8k tokens per minute). The limit is a **rolling 24-hour bucket that refills continuously** (about 8.3k tokens per hour), not a reset at midnight. The local ledger mirrors this and syncs to the usage Groq reports whenever it refuses a call. `pulse budget` shows tokens left and when the next run fits.

The pipeline uses two models so one run cannot use up the whole quota:

| Work | Model | Ledger file | Tokens per run |
|---|---|---|---|
| Theme discovery, residual naming, compose | `openai/gpt-oss-120b` | `data/llm_budget.json` | about 20-30k |
| Per-review classification (sample of 500 + 250 residual) | `openai/gpt-oss-20b` | `data/llm_budget_classify.json` | about 55-60k |

- Only a capped sample of fresh reviews goes to the LLM (`llm.classify.max_reviews_per_run`, `max_residual_reviews`). The rest are labelled by a small scikit-learn model trained on the LLM's own answers (cached ones included), which costs no tokens. With too few LLM labels to learn from, they fall back to the nearest theme by TF-IDF centroid. Every review still gets exactly one theme.
- LLM answers are cached per review, theme set, prompt and model (`data/cache/`), so re-running the same week with the same themes is nearly free. A changed theme set means a new cache and a cold run.
- About 3 cold runs fit per day (`llm.budget.max_runs_per_day`). This run-count cap is separate from the token check; both must pass.
- A cold run takes about 15-20 minutes because calls are paced under the tokens-per-minute limit. A fully cached re-run takes about a minute.
- If Groq answers 401/403/404 (for example a model name your key cannot use), the run stops at once. A bad-JSON reply (400 `json_validate_failed`) only sends that batch to the fallback. List the models your key can use with `GET https://api.groq.com/openai/v1/models`.

---

## 9. Theming in detail

1. **Embed**: reviews become vectors with local TF-IDF + truncated SVD (no API key, no PyTorch).
2. **Cluster and sample**: KMeans over-clusters the reviews and a stratified sample goes to the LLM.
3. **Discover**: the LLM names 3-5 functional product themes (for example "Customer Support & Issue Resolution"). Generic sentiment buckets such as "Positive feedback" are banned by the prompt and a validator. Previous week's themes are passed in for name stability.
4. **Classify**: every review gets exactly one theme or `other`, in batches, on the classify model. Reviews not sent to the LLM, or the LLM was unsure about, are labelled by the trained fallback model (see section 8).
5. **Residual pass**: if `other` is large and a theme slot is free, one more theme is looked for inside `other`.
6. **Rank**: themes are ranked by volume (or `balanced` store weighting), with low-rating share and helpful votes as tie-breakers. `other` is never in the Top 3. Week-over-week change is matched by theme-name similarity.

---

## 10. Guardrails and evals

**Guardrails** run before publishing, and any hard failure blocks it. They cover: theme count, exactly 3 quotes, each quote verbatim, PII re-scan, 250-word limit, 3 actions linked to Top-3 themes, no internal identifiers, quote-to-store attribution, quote rules, plus soft checks for source coverage and tone.

**Evals** (`pulse eval`, offline and free by default) check a week's recorded artifacts against the targets in `Docs/eval.md`: compliance (hard gates), theme quality, publishing against a fake in-process MCP server, and PII fixtures. `--live` and `--judge` add stability and LLM-as-judge checks and cost tokens. Edge cases are catalogued with IDs in `Docs/edge-case.md` and each P0/P1 case has a test named after its ID.

---

## 11. Privacy and security

- **PII**: reviews are scrubbed before anything is stored or sent to the LLM. Review IDs are pseudonymised. Raw reviews and author names are never saved. The final pulse is re-scanned before publishing.
- **No Google code in this repo**: Docs and Gmail go through the MCP server, which owns the Google login. Only tools in the allow-list in `config/mcp_servers.yaml` can be called, and `gmail_send_email` is reachable only when sending is explicitly requested.
- **Draft by default**: the email is a draft unless `--send` (or send mode) is chosen.
- **Secrets**: only in `.env` or the host's environment, never in git. A pre-commit hook blocks committing `.env` and private keys.
- **API access**: the browser talks only to Vercel. The Vercel proxy adds `PULSE_API_TOKEN` server-side, and the Railway API rejects calls without it.

---

## 12. Scheduling

- **Railway scheduler** (`src/pulse/scheduler.py`): started by `scripts/railway_start.sh` when `PULSE_SCHEDULE_ENABLED=1`. It checks hourly and, on Monday at or after 09:00 IST, runs the last completed week if it has not already succeeded. It only publishes when `PULSE_SCHEDULE_LIVE=1`; otherwise it does a dry run. A spent token budget skips the run with a warning.
- **Windows Task Scheduler**: `scripts/run_weekly.ps1`, `scripts/schedule_task.ps1` and `scripts/scheduled_task.xml` set up a Monday 09:00 IST run on a local machine.

---

## 13. Deployment

```
Browser ──► Vercel: static React app (web/dist) + /api proxy (adds the secret token)
                         │
                         ▼
            Railway: FastAPI (uvicorn) + Volume mounted at /data
                ├─ pipeline runs, weekly scheduler
                └─ Doc + Gmail via the MCP server
```

- **Railway** builds the `Dockerfile` (Python 3.12, locked deps with `uv`). `railway.json` sets the health check `/api/health`. The volume is seeded once from `deploy_data/runs` on first boot and never overwritten afterwards.
- **Vercel** builds `web/` and serves `dist`; `web/api/proxy.js` forwards `/api/*` to Railway.
- Step-by-step setup, required environment variables and update behaviour are in `Docs/deployment-plan.md`.

---

## 14. Testing and code quality

```powershell
.venv\Scripts\python -m pytest -q          # unit tests
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m ruff format --check src tests
.venv\Scripts\python -m mypy               # type checking (src/pulse)
.venv\Scripts\pre-commit run --all-files   # same checks as the commit hook
```

Unit tests mock HTTP with respx and use frozen fixtures in `tests/fixtures/` and `evals/fixtures/`. Integration tests that hit real services are marked `integration` and skipped by default.

---

## 15. Known limitations

- **`other` is high.** On week 2026-W42 the latest run left about 52% of reviews in `other` against the 20% target (EV-THM-02, a quality gate). Most of it is generic praise that fits none of the problem themes, plus the effect of sampling only part of the reviews with the LLM to stay inside the free tier. The trained fallback model reduces it but does not remove it.
- **Re-running discovery when `other` is above 40%** (EC-THM-11 in `Docs/edge-case.md`) is specified but not implemented yet.
- **Classification accuracy eval (EV-THM-01)** uses golden labels that are an unreviewed keyword-rule draft, so the score is indicative only.
- **Token cost vs coverage.** A cold run costs about 75-90k tokens. Labelling every review with the LLM would cost far more than the free tier allows, which is why sampling is used.
- **App Store reach.** The public RSS feed returns about 500 recent reviews, so older weeks depend on accumulated snapshots.

---

## 16. Further reading

| Document | Contents |
|---|---|
| `Docs/problemStatement.md` | The original brief and definition of done |
| `Docs/architecture.md` | Full design: components, data models, prompts, error handling |
| `Docs/edge-case.md` | Edge cases by area with IDs and priorities |
| `Docs/eval.md` | Eval framework, targets and gates |
| `Docs/deployment-plan.md` | Railway + Vercel setup |
| `Docs/implementation-plan.md`, `Docs/phase0-spikes.md` | Build plan and early spikes |
