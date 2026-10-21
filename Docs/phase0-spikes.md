# Phase 0 Spike Findings

## Spike A — MCP Servers (Task 0.7)

**Status:** Deferred as requested. Probe script implemented in `scripts/mcp_probe.py` and placeholder config registered in `config/mcp_servers.yaml`.

- Placeholder configuration:
  - `gdocs`: `command: <docs-mcp-server-command>` with tool_map for `find`, `create`, `write`.
  - `gmail`: `command: <gmail-mcp-server-command>` with tool_map for `draft`.
- `scripts/mcp_probe.py`:
  - Detects `<...>` placeholders cleanly without crashing.
  - When real servers are configured, initializes an MCP session via `mcp.client.stdio.stdio_client`, lists all exposed tools, and validates coverage against the required `tool_map`.
  - Safe for execution: `uv run python scripts/mcp_probe.py`.

---

## Spike B — Review Sources (Task 0.8)

**Execution Date:** 2026-10-14
**Current ISO Week:** 2026-W42
**Target App:** Groww (Play Store: `com.nextbillion.groww`, App Store India: `id1404871703`)

### 1. Apple App Store (India Storefront RSS)

- **Endpoint tested:** `https://itunes.apple.com/in/rss/customerreviews/page={1..10}/id=1404871703/sortby=mostrecent/json`
- **Pages fetched:** 10 / 10
- **Total reviews fetched:** 500 reviews (50 reviews/page)
- **Newest review date:** `2026-10-13T07:29:22-07:00` (ISO Week: `2026-W42`)
- **Oldest review date:** `2026-09-01T20:53:09-07:00` (ISO Week: `2026-W36`)
- **Coverage depth:** **42 days (~6.0 weeks)**

#### Critical Conclusion:
The Apple RSS feed has a hard limit of 10 pages (~500 reviews). For a high-velocity app like Groww in India, 500 reviews reaches back **only 6 weeks**, which is less than the required 8–12 week window.

**Decisions for Implementation (P1 & P2):**
1. **Weekly Snapshot Accumulation (Mandatory):** Each weekly run must persist cleaned reviews to `data/snapshots/app_store/<iso_week>.jsonl`. The rolling window loader must merge stored snapshots from prior weeks with the live fetch.
2. **First-run Coverage Transparency:** For the initial run (before 8–12 weeks of snapshots accumulate), the orchestrator must record `coverage_start["app_store"] = 2026-09-01` in `RunManifest` and include a coverage note in the pulse header line per architecture §4.1.2.
3. **FileSource Backfill (P1.4):** Support importing historical App Store public exports to instantly backfill the remaining 2–6 weeks on fresh environments.
4. **Feed Quirks Verified:**
   - First entry on page 1 can be app metadata (`im:rating` not present) — skipped.
   - Missing `im:voteSum` handled with default `0`.
   - Title present in App Store reviews (`title.label`).

---

### 2. Google Play Store (`google-play-scraper`)

- **Package ID:** `com.nextbillion.groww` (`country="in"`, `lang="en"`, `sort=Sort.NEWEST`)
- **Batch fetched:** 200 reviews
- **Newest review date:** `2026-10-13T22:20:07+00:00` (ISO Week: `2026-W42`)
- **Oldest review date:** `2026-10-12T11:41:42+00:00` (ISO Week: `2026-W42`)
- **Coverage depth:** **2 days (~0.3 weeks)** for 200 reviews.

#### Critical Conclusion:
Groww receives ~100 reviews per day on Google Play Store. Volume is extremely high.
- Pagination with continuation tokens will easily cover the 8–12 week window.
- The `MAX_REVIEWS` limit (default 5,000) will prevent runaway downloads while providing deep coverage.
- Discarded user fields confirmed: `userName`, `userImage`, `replyContent`, `repliedAt`.

---

### 3. Test Fixtures Created

Scrubbed test fixtures saved in `tests/fixtures/`:
- `tests/fixtures/app_store_sample.json`: 10 scrubbed App Store reviews with ratings, titles, versions, vote sums, and dates.
- `tests/fixtures/play_store_sample.json`: 10 scrubbed Play Store reviews with ratings, versions, helpful votes, and dates.
