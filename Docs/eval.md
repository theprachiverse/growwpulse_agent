# Evaluation Plan — Groww Weekly Review Pulse Agent

> Based on: [problemStatement.md](./problemStatement.md) · [architecture.md §4.5, §8](./architecture.md) · [implementation-plan.md Phase 6](./implementation-plan.md)
> Companion doc: [edge-case.md](./edge-case.md) (the `EC-*` IDs referenced below)

This document defines how we prove the pulse agent meets its requirements: what we measure, on which data, how each metric is computed, what the pass bar is, and how results are reported and used as a CI gate.

---

## 1. Two Layers of Evaluation

| Layer | When it runs | Data | Purpose | Output |
|---|---|---|---|---|
| **Per-run guardrails** | Every `pulse run`, before publishing | That week's real reviews | Block a non-compliant pulse from being published (fail closed) | `data/runs/<week>/eval_report.json` |
| **Offline eval suite** | `pulse eval`, on every PR (CI) and before releases | Frozen fixtures + golden labels | Measure quality, catch regressions, test adversarial and edge cases | `evals/reports/<timestamp>.json` + console table |

The guardrails answer *"is this week's pulse safe to publish?"*. The offline suite answers *"is the system good, and did a change make it worse?"*. Both use the **same** checking code (`guardrails/validators.py`, `compose/shorten.count_pulse_body_words`) so the two can never disagree.

---

## 2. Requirement Traceability

Every requirement in the problem statement maps to at least one eval.

| Problem statement requirement | Eval(s) | Gate |
|---|---|---|
| Public reviews only, 8–12 week window | EV-ING-01, EV-ING-02 | Hard |
| PII scrubbed before any output | EV-PII-01 … EV-PII-05 | Hard |
| ≤ 5 themes | EV-CMP-01 | Hard |
| Top 3 themes, ranked by volume | EV-CMP-01, EV-THM-04 | Hard |
| 3 verbatim quotes | EV-CMP-02, EV-CMP-03 | Hard |
| 3 action ideas, based on the themes | EV-CMP-05, EV-JDG-02 | Hard / Soft |
| ≤ 250 words | EV-CMP-04 | Hard |
| Themes fit the product | EV-THM-01, EV-THM-02, EV-JDG-01 | Quality |
| Google Doc created/updated via MCP | EV-PUB-01, EV-PUB-02 | Integration |
| Gmail draft via MCP, never sent | EV-PUB-03, EV-PUB-04 | Hard |
| No direct Google API code | EV-PUB-05 | Hard |

---

## 3. Datasets & Fixtures

All fixtures are **already scrubbed** (except the PII injection set, which is synthetic). No real reviewer data is stored in `evals/`.

| File | Size | Contents | Used by |
|---|---|---|---|
| `evals/fixtures/reviews_sample.jsonl` | ~500 reviews | Frozen `CleanReview` records from both stores. Target mix ≈ 85% Play / 15% App Store, all 5 ratings, ≥ 6 weeks spread. Must include ≥ 20 iOS-specific reviews (Face ID, iOS updates) for EV-THM-05. | Compliance, theming, stability |
| `evals/fixtures/pii_injection.jsonl` | ~50–80 cases | Synthetic reviews with injected PII. Each row: `{id, text, title?, expected_types[], tier, category}`. | EV-PII-* |
| `evals/fixtures/pii_negatives.jsonl` | ~40 cases | Text that must **not** be scrubbed (prices, versions, dates, strike prices). Each row: `{id, text}`. Expected output equals input. | EV-PII-04 |
| `evals/golden/theme_labels.csv` | ~200 rows | `rid, text, golden_theme, labeller, notes`. Labels come from a fixed golden taxonomy (below). | EV-THM-01 |
| `evals/fixtures/llm_recordings/` | per step | Recorded LLM JSON responses so the compliance suite runs offline and deterministically. | `--offline` mode |
| `evals/fixtures/edge_cases/` | 1 file per `EC-*` group | Small inputs that reproduce each edge case in [edge-case.md](./edge-case.md). | EV-EDGE-* |

### 3.1 Golden taxonomy

Discovered themes change names each run, so golden labels use a **fixed** taxonomy. Discovered themes are mapped onto it during scoring (§5.3).

| Golden label | Covers |
|---|---|
| `trading_execution` | Orders, F&O, stop-loss, GTT, price/chart lag at market open |
| `customer_support` | Bot loops, no human agent, slow ticket replies |
| `app_stability_login` | Crashes, freezes, biometric / Face ID / OTP login |
| `charges_brokerage` | Hidden charges, brokerage, DP charges, transparency |
| `payments_withdrawals` | UPI deposits, add-money failures, withdrawal delays |
| `mf_onboarding_kyc` | Mutual funds, SIP, account opening, KYC |
| `other` | Praise with no specifics, off-topic, unclear |

### 3.2 Labelling rules

- Two people label independently. Disagreements are settled by discussion, and the agreement rate (Cohen's κ) is recorded in the CSV header. Target κ ≥ 0.7. If it's lower, the taxonomy definitions need tightening before the set is used.
- One label per review: the **main** complaint. A review that mentions two topics gets the one it spends the most words on.
- The golden set is versioned (`theme_labels_v1.csv`). Changing it requires a new version and a re-baselined report.

### 3.3 PII injection fixture categories

| Tier | Category | Examples | Why |
|---|---|---|---|
| **A** (regex-covered, must be 100%) | Phone | `+91 98765 43210`, `09876543210`, `(9876543210)` | EC-PII-01, 09 |
| A | PAN | `ABCDE1234F`, `abcde1234f` | EC-PII-02 |
| A | Aadhaar | `1234 5678 9012`, `1234-5678-9012` | EC-PII-03 |
| A | Email | `a.b+groww@gmail.com` | EC-PII-04 |
| A | UPI | `rahul@okaxis`, `9876543210@ybl` | EC-PII-05 |
| A | IFSC / account / demat ID | `HDFC0001234`, `1208160012345678` | EC-PII-06, 07, 13 |
| A | URL | `https://wa.me/919876543210` | EC-PII-08 |
| A | In App Store title only | title `Call 9876543210` | EC-PRE-08 |
| A | Hidden by zero-width chars | `98765​43210` | EC-PRE-02 |
| **B** (needs extra recognizers, tracked) | Person names | `my name is Rahul Sharma`, `agent Priya` | EC-PII-10 |
| B | Order / ticket IDs | `Order GRW2410AB93XZ` | EC-PII-11 |
| B | Device IDs | UUIDs, IMEI | EC-PII-12 |
| B | Obfuscated digits | `9 8 7 6…`, `nine eight seven…`, Devanagari digits | EC-PII-14, EC-PRE-03 |
| B | Hinglish context | `mera number 9876543210 hai` | Plan 6.1 |

Each fixture row also places PII in different positions (start, middle, end of sentence, next to punctuation).

---

## 4. Eval Catalog

**Gate** column: **Hard** = fails CI and blocks publishing. **Quality** = fails CI if below target, but never blocks a weekly run on its own. **Soft** = reported as a warning. **Track** = reported only, no target yet.

### 4.1 Compliance evals (hard constraints)

| ID | Eval | Method | Metric | Target | Gate |
|---|---|---|---|---|---|
| EV-ING-01 | Window bounds | Every review in `reviews_clean.jsonl` has `window_start ≤ date ≤ window_end`, and `window_weeks ∈ [8, 12]` | violations | 0 | Hard |
| EV-ING-02 | No raw user fields | Grep all files under `data/` and `evals/reports/` for raw `reviewId` formats, `userName`, `author`, avatar URLs | hits | 0 | Hard |
| EV-PII-01 | Scrubber recall, tier A | Run `scrub_pii` on every tier-A fixture. A case passes when every injected value is gone **and** the expected placeholder type is present. | recall per category | 100% | Hard |
| EV-PII-02 | End-to-end leak test | Mix injection fixtures into `reviews_sample.jsonl`, run the full pipeline (offline LLM), then re-scan `pulse.md`, `pulse.json`, `themes.json`, logs, and the email body (minus the Doc link) with **all** recognizers. Also check that no injected raw value appears as a substring. | leaks | 0 | Hard |
| EV-PII-03 | Scrubber recall, tier B | Same as EV-PII-01 on tier-B cases. | recall per category | Track (target set once G2–G4 in [edge-case.md §8](./edge-case.md) are fixed) | Track |
| EV-PII-04 | False-positive rate | Run `scrub_pii` on `pii_negatives.jsonl`. | % changed | ≤ 2%, and 0% on prices, versions, strike prices | Quality |
| EV-PII-05 | Placeholder idempotence | `scrub(scrub(x)) == scrub(x)` on all fixtures | mismatches | 0 | Hard |
| EV-CMP-01 | Theme count | `1 ≤ len(themes) ≤ 5`, exactly 3 Top themes, none is `other` | pass/fail | pass | Hard |
| EV-CMP-02 | Quote faithfulness | Each `Quote.text` is an exact (byte-for-byte) substring of the `clean_text` of the `rid` it cites | exact-match rate | 100% | Hard |
| EV-CMP-03 | Quote rules | 3 quotes, 3 distinct `rid`s, one per Top-3 theme, 8–40 words each, store label on the quote's own line matches `source` | violations | 0 | Hard |
| EV-CMP-04 | Length compliance | `count_pulse_body_words(pulse.md)` (rule in §5.1) | words | ≤ 250 | Hard |
| EV-CMP-05 | Action count & linking | Exactly 3 actions, one per Top-3 theme, valid `owner_hint` | violations | 0 | Hard |
| EV-CMP-06 | No identifiers | No `rid_`, raw review IDs, or hex hashes in rendered output | hits | 0 | Hard |
| EV-CMP-07 | Shortening never edits quotes | Feed an over-long fixture (≥ 320 words), then compare quotes before and after shortening | changed quotes | 0 | Hard |

### 4.2 Theme quality evals

| ID | Eval | Method | Metric | Target | Gate |
|---|---|---|---|---|---|
| EV-THM-01 | Classification accuracy | Run theming on the golden set, map discovered themes to golden labels (§5.3), score against human labels | macro-F1 | ≥ 0.75 | Quality |
| EV-THM-02 | Coverage | Share of reviews assigned to `other` | % other | ≤ 20% | Quality |
| EV-THM-03 | Stability | Run theming twice on the same data with the same config (§5.4) | mean matched Jaccard | ≥ 0.70 | Quality |
| EV-THM-04 | Ranking correctness | Top 3 equals the 3 largest non-`other` themes by the configured weighting, with tie-breakers applied in order | pass/fail | pass | Hard |
| EV-THM-05 | iOS visibility | On a fixture with a planted iOS-only issue (~40% of App Store reviews, ~4% overall), check that it is a discovered theme, and that it reaches the Top 3 under `balanced` weighting | pass/fail | pass | Quality |
| EV-THM-06 | No generic themes | Theme names checked against a ban list (`positive`, `negative`, `general`, `feedback`, `complaints`, `misc`) | hits | 0 | Quality |
| EV-THM-07 | Theme distinctness | Max pairwise cosine similarity between theme `name + definition` embeddings | max similarity | ≤ 0.80 | Soft |
| EV-THM-08 | Centroid-fallback rate | Share of reviews classified by fallback instead of the LLM | % | Track (alert if > 15%) | Track |

### 4.3 LLM-as-judge evals

Rubrics and setup are in §6.

| ID | Eval | Input to judge | Metric | Target | Gate |
|---|---|---|---|---|---|
| EV-JDG-01 | Theme coherence | Theme name + definition + 10 random member reviews | mean score 1–5 across themes | ≥ 4.0 | Quality |
| EV-JDG-02 | Action groundedness | Action + linked theme stats + that theme's quotes and 10 member reviews | mean 1–5 | ≥ 4.0 | Quality |
| EV-JDG-03 | Pulse usefulness | Full `pulse.md` | mean 1–5 | ≥ 4.0 | Quality |
| EV-JDG-04 | Factual consistency | `pulse.md` + `themes.json` stats. Every number, share, and rating in the text must match the stats. | unsupported claims | 0 | Soft |
| EV-JDG-05 | Quote representativeness | Quote + its theme definition | % judged "on theme" | ≥ 90% | Soft |

### 4.4 Publishing evals (MCP)

| ID | Eval | Method | Target | Gate |
|---|---|---|---|---|
| EV-PUB-01 | Doc create then update | Fake in-process MCP server records calls. First run calls `create`, second run for the same week calls `write` on the same `doc_id` and never `create`. | pass | Hard |
| EV-PUB-02 | Doc content equals pulse | Text written to the Doc contains all 3 quotes byte-for-byte and all section headings | pass | Hard |
| EV-PUB-03 | Draft only, never send | Over every test, the recorded tool calls contain no tool outside the allow-list (no `send*`) | 0 send calls | Hard |
| EV-PUB-04 | Draft content | Draft has the right subject, recipient = `PULSE_RECIPIENT`, full pulse, the Doc link, and the per-store footer | pass | Hard |
| EV-PUB-05 | No Google API code | Grep `src/` for `googleapiclient`, `google.oauth2`, `google_auth_oauthlib`, `gmail.googleapis.com`, `docs.googleapis.com` | 0 hits | Hard |
| EV-PUB-06 | Fail-fast on missing tool | `tool_map` names a tool the fake server doesn't expose, so the run fails before any write | pass | Hard |
| EV-PUB-07 | Real servers (manual) | Against a sandbox recipient: doc created, draft created, re-run updates the doc | pass | Manual, before release |

### 4.5 Edge-case regression suite

Every `P0` and `P1` edge case in [edge-case.md](./edge-case.md) gets a test named after its ID (e.g. `test_ec_cmp_31_shorten_never_edits_quotes`). The suite reports:

| ID | Metric | Target | Gate |
|---|---|---|---|
| EV-EDGE-01 | P0 edge cases with a passing test | 100% | Hard |
| EV-EDGE-02 | P1 edge cases with a passing test | ≥ 90% | Quality |
| EV-EDGE-03 | P2 edge cases with a test | Track | Track |

### 4.6 Operational metrics (tracked, not gated)

| Metric | Source | Why |
|---|---|---|
| Tokens in/out per step, estimated cost per run | `RunManifest.llm_usage`; daily totals per model in `llm_budget.json` and `llm_budget_classify.json` | Catch cost regressions from prompt changes; keep a cold run inside Groq's free daily quota |
| Wall time per step | structlog step timings | Catch slow steps before the weekly schedule is affected |
| Repair / shorten passes used | manifest | Rising repairs mean prompts are drifting |
| Coverage per source (weeks reached) | `coverage_start` | App Store snapshot accumulation working as expected |
| Reviews dropped by language filter | preprocess counts | Watch for Hinglish loss (EC-PRE-05) |

---

## 5. Metric Definitions

### 5.1 Word count (EV-CMP-04)

One function, `compose.shorten.count_pulse_body_words`, is used by both the guardrail and the eval.

- **Excluded:** the `# title` line and the `*Window: …*` header metadata line.
- **Counted:** everything else, including headings (`## Top Themes`), quote text, and attribution (`— 1★, Play Store, Oct 2026`).
- **Token rule:** split on whitespace. A token counts as a word if it contains at least one letter or digit. So `—` and `>` are not words; `1–2★`, `24.0%`, and `(Play:` are.

### 5.2 PII recall and leaks (EV-PII-01/02)

- **Case passes** when every injected raw value is absent from the output **and** each `expected_types` placeholder appears.
- **Recall per category** = passing cases ÷ cases in that category.
- **Leak** (EV-PII-02) = any injected raw value, or any recognizer hit, found in any output artifact. A single leak fails the suite.

### 5.3 Theme macro-F1 (EV-THM-01)

1. Build a contingency matrix: discovered theme × golden label, counting reviews.
2. Use the Hungarian algorithm (`scipy.optimize.linear_sum_assignment`) to find the one-to-one mapping from discovered themes to golden labels that maximises agreement. Discovered `other` always maps to golden `other`.
3. Golden labels with no matched theme score F1 = 0 (the system missed that topic). Discovered themes with no golden match map to `other`.
4. Report macro-F1 over golden labels **excluding** `other`, plus per-label precision, recall, and F1.

Since the system can find at most 5 themes and the golden taxonomy has 6 non-`other` labels, one label may always be unmatched. This is expected. Per-label results show which topic was merged or missed.

### 5.4 Stability (EV-THM-03)

1. Run discover + classify twice (A, B) on the same input, with the same config and seeds. LLM temperature stays as configured; this measures real run-to-run variance.
2. Match themes in A to themes in B with the Hungarian algorithm on review overlap.
3. For each matched pair, Jaccard = |reviews in A ∩ B| ÷ |reviews in A ∪ B|.
4. Score = mean Jaccard over matched pairs, weighted by theme size. Unmatched themes count as Jaccard 0.
5. Also report whether the **Top 3** set is the same in both runs (Track).

### 5.5 Ranking (EV-THM-04)

Recompute the ranking independently from `assignments.jsonl`:
`volume` → sort by `review_count`; `balanced` → sort by the mean of per-source shares over sources that have reviews. Then apply tie-breakers in order: `low_rating_share` ↓, `sum(helpful_votes)` ↓, `theme_id` ↑. The result must equal the Top 3 in `themes.json`.

---

## 6. LLM-as-Judge

### 6.1 Setup

| Setting | Value |
|---|---|
| Judge model | Pinned and recorded in every report (`judge_model`, `judge_prompt_version`). Prefer a **different, stronger** model than the generator to reduce self-preference. |
| Temperature | 0 |
| Output | Strict JSON: `{"score": 1-5, "reason": "<one sentence>", "evidence": ["<rid or quote>"]}`, parsed with Pydantic |
| Repeats | Each item judged 3 times; the median score is used. Spread > 1 point is logged as low agreement. |
| Prompts | `evals/prompts/judge_*.md`, versioned like the pipeline prompts |
| Input | Scrubbed data only, same as the pipeline |

### 6.2 Rubrics

**EV-JDG-01 Theme coherence** — *"Do these 10 reviews belong to theme X as defined?"*

| Score | Meaning |
|---|---|
| 5 | All or nearly all reviews clearly fit the definition |
| 4 | Most fit; 1–2 are borderline |
| 3 | About half fit; the theme is too broad or mixes topics |
| 2 | Few fit; the definition doesn't describe the members |
| 1 | No clear relationship |

**EV-JDG-02 Action groundedness** — *"Is this action supported by the theme's evidence, and is it specific to Groww?"*

| Score | Meaning |
|---|---|
| 5 | Directly addresses the main complaint in the evidence; specific and doable in a sprint or quarter |
| 4 | Grounded and specific, but addresses a secondary point |
| 3 | Grounded but generic ("improve support response times") |
| 2 | Loosely related to the theme |
| 1 | Not supported by the evidence, or invents facts |

**EV-JDG-03 Pulse usefulness** — *"Could a PM, a support lead, or an executive read this in under 2 minutes and know what to do?"*

| Score | Meaning |
|---|---|
| 5 | Clear priorities, evidence and actions connect, easy to scan |
| 4 | Useful, with minor clutter or vagueness |
| 3 | Readable but the actions or priorities are unclear |
| 2 | Hard to scan or mostly generic |
| 1 | Not usable |

### 6.3 Judge calibration

Before trusting a judge, check it against people:

1. Two people score 20 items per rubric using the same rubric text.
2. Compute agreement between the judge median and the human mean (Spearman ρ, plus % within ±1 point).
3. Accept the judge when ρ ≥ 0.6 and ≥ 80% of items are within ±1. Otherwise revise the rubric wording and repeat.
4. Store the calibration result in `evals/reports/judge_calibration_<version>.json`. Re-run it when the judge model or prompt changes.

---

## 7. Reporting

### 7.1 Console output

```
pulse eval — 2026-10-18T10:12:03Z — suite=all — offline=true
┌───────────────┬────────────────────────────┬─────────┬──────────┬────────┐
│ ID            │ Eval                       │ Value   │ Target   │ Result │
├───────────────┼────────────────────────────┼─────────┼──────────┼────────┤
│ EV-CMP-02     │ Quote faithfulness         │ 100%    │ 100%     │ PASS   │
│ EV-PII-02     │ End-to-end PII leaks       │ 0       │ 0        │ PASS   │
│ EV-THM-01     │ Theme macro-F1             │ 0.78    │ ≥ 0.75   │ PASS   │
│ EV-JDG-02     │ Action groundedness        │ 3.8     │ ≥ 4.0    │ FAIL   │
│ …             │                            │         │          │        │
└───────────────┴────────────────────────────┴─────────┴──────────┴────────┘
Hard: 18/18 pass · Quality: 9/10 pass · Soft warnings: 1 · Exit code: 1
```

### 7.2 Report file (`evals/reports/<timestamp>.json`)

```json
{
  "run_at": "2026-10-18T10:12:03Z",
  "git_sha": "abc1234",
  "suite": "all",
  "offline": true,
  "config_hash": "sha256:…",
  "versions": {
    "prompts": {"discover": "v3", "classify": "v2", "select_quotes": "v2", "actions": "v2", "shorten": "v1"},
    "chat_model": "openai/gpt-oss-120b",
    "embed_model": "sentence-transformers/all-MiniLM-L6-v2",
    "judge_model": "<pinned judge>",
    "judge_prompt_version": "v1",
    "golden_set": "theme_labels_v1"
  },
  "results": [
    {"id": "EV-CMP-02", "gate": "hard", "value": 1.0, "target": "== 1.0", "passed": true, "details": {}},
    {"id": "EV-THM-01", "gate": "quality", "value": 0.78, "target": ">= 0.75", "passed": true,
     "details": {"per_label_f1": {"trading_execution": 0.84, "customer_support": 0.81}}}
  ],
  "summary": {"hard_passed": 18, "hard_total": 18, "quality_passed": 9, "quality_total": 10, "soft_warnings": 1},
  "exit_code": 1
}
```

Reports contain metric values and IDs only. They must not contain review text beyond what is already in the scrubbed fixtures.

### 7.3 Exit codes

| Code | Meaning |
|---|---|
| 0 | All hard and quality targets met |
| 1 | At least one hard or quality target missed |
| 2 | The eval harness itself failed (missing fixture, judge parse error after retries, crash) |

---

## 8. How to Run

```bash
# Full suite, using recorded LLM responses (fast, deterministic, no API cost) — default in CI
uv run pulse eval --offline

# Only the hard compliance checks
uv run pulse eval --suite compliance --offline

# Quality suite with the live LLM and judges (costs tokens; run before releases or prompt changes)
uv run pulse eval --suite quality --live --judge

# Edge-case regression tests only
uv run pytest -k "test_ec_"

# Re-check one week's real pulse against the guardrails without publishing
uv run pulse run --week 2026-W42 --dry-run
```

> `pulse eval` is a stub today ([cli.py](../src/pulse/cli.py)). The flags above are the target interface for Phase 6.

| Trigger | Suites |
|---|---|
| Every PR | `compliance` + edge-case tests, offline |
| Changes to prompts, theming, or compose | + `quality`, live LLM, with judges |
| Before a release / schedule change | Full live suite + EV-PUB-07 manual check |
| Every weekly run | Per-run guardrails (§1) |

---

## 9. Baselines & Regression Policy

- The first green report is committed as `evals/reports/baseline.json` (Plan 6.4 exit criteria).
- A PR **fails** if any hard eval fails, or any quality metric drops below target.
- A PR gets a **warning** if a quality metric drops by more than 0.03 (F1, Jaccard) or 0.3 (judge score) compared to the baseline, even if it stays above target.
- The baseline is updated only on purpose (a commit that says why), for example after a golden-set version change or an accepted model switch.
- Every bug found in a real weekly run gets an `EC-*` entry in [edge-case.md](./edge-case.md) and a fixture in `evals/fixtures/edge_cases/` before it is fixed.

---

## 10. File Layout

```text
evals/
├── run_evals.py              # entry point used by `pulse eval`
├── metrics.py                # word count, F1 + Hungarian matching, Jaccard, PII recall
├── judges.py                 # LLM-as-judge runner (3 repeats, median)
├── prompts/
│   ├── judge_theme_coherence.md
│   ├── judge_action_groundedness.md
│   └── judge_pulse_usefulness.md
├── fixtures/
│   ├── reviews_sample.jsonl
│   ├── pii_injection.jsonl
│   ├── pii_negatives.jsonl
│   ├── llm_recordings/
│   └── edge_cases/
├── golden/
│   └── theme_labels_v1.csv
└── reports/
    ├── baseline.json
    └── judge_calibration_v1.json
```
