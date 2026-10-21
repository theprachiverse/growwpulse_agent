# Groww Weekly Review Pulse

Turns public Groww app reviews (Google Play and the Apple App Store) into a one-page weekly pulse: top 3 themes, 3 verbatim user quotes, and 3 action ideas (250 words or fewer, no PII). The pulse is written to a Google Doc and a Gmail draft through an MCP server. See `Docs/problemStatement.txt` for the brief and `Docs/architecture.md` for the design.

## Quick start

```powershell
copy .env.example .env          # add LLM_API_KEY (Groq) and MCP_AUTH_TOKEN
.venv\Scripts\pulse.exe run --dry-run --skip-health-check   # full pipeline, nothing published
.venv\Scripts\pulse.exe budget                              # tokens and runs left today
.venv\Scripts\python -m pytest -q                           # tests
```

Behaviour lives in `config/config.yaml`; secrets live in `.env`.

## LLM budget (Groq free tier)

Groq's free tier gives about 200k tokens/day per model (8k tokens/minute). The limit is a **rolling 24-hour bucket that refills continuously** (about 8.3k tokens/hour), not a reset at midnight. The ledger mirrors this and syncs to the usage Groq reports whenever it refuses a call. `pulse budget` shows tokens left and when the next run fits. The pipeline uses two models so one run cannot use up the whole quota:

| Work | Model | Ledger file | Tokens per run |
|---|---|---|---|
| Theme discovery, residual naming, compose | `openai/gpt-oss-120b` | `data/llm_budget.json` | ~20-30k |
| Per-review classification (sample of 500 + 250 residual) | `openai/gpt-oss-20b` | `data/llm_budget_classify.json` | ~55-60k |

- Only a capped sample of fresh reviews goes to the LLM (`llm.classify.max_reviews_per_run`, `max_residual_reviews`). The rest are assigned to the nearest theme by TF-IDF centroid, which costs no tokens. Every review still gets one theme.
- About 3 cold runs fit per day (`llm.budget.max_runs_per_day`). Re-running the same week reuses cached labels.
- A run takes roughly 15-20 minutes because calls are paced under the tokens-per-minute limit.
- If Groq answers 401/403/404 (for example a model name your key cannot use), the run stops at once. A bad-JSON reply (400 `json_validate_failed`) only sends that batch to the centroid fallback. List the models your key can use with `GET https://api.groq.com/openai/v1/models`.
