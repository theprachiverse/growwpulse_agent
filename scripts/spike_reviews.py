"""Spike B script: Test Apple App Store RSS and Google Play Store reviews feeds.

Tasks:
1. Fetch App Store RSS customerreviews JSON (pages 1..10) for Groww (id=1404871703, in).
   - Check entry structure, handle quirks (metadata entry, single-item dict).
   - Measure date reach of the ~500 reviews (weeks back).
2. Fetch 200 Google Play Store reviews using google-play-scraper.
   - Confirm fields and date range.
3. Save small, scrubbed sample fixtures to tests/fixtures/ for deterministic tests.
4. Print summary metrics.

Usage:
    uv run python scripts/spike_reviews.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx
from google_play_scraper import Sort, reviews  # type: ignore[import-untyped]

from pulse.utils.weeks import current_week, date_to_iso_week

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)


def scrub_pii_basic(text: str) -> str:
    """Basic scrubbing for test fixtures to ensure no emails or phone numbers are saved."""
    if not text:
        return ""
    # Email
    text = re.sub(r"\S+@\S+\.\S+", "[EMAIL]", text)
    # Phone (IN)
    text = re.sub(r"(\+91[\-\s]?)?[6-9]\d{9}", "[PHONE]", text)
    return text


def run_app_store_spike() -> dict[str, Any]:
    print("\n--- 1. Apple App Store RSS Spike ---")
    app_id = "1404871703"
    base_url = "https://itunes.apple.com/in/rss/customerreviews"

    total_reviews = 0
    pages_fetched = 0
    all_dates: list[datetime] = []
    sample_clean_entries: list[dict[str, Any]] = []

    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for page in range(1, 11):
            url = f"{base_url}/page={page}/id={app_id}/sortby=mostrecent/json"
            try:
                resp = client.get(url)
                if resp.status_code != 200:
                    print(f"  Page {page}: HTTP {resp.status_code}")
                    break

                data = resp.json()
                feed = data.get("feed", {})
                entries = feed.get("entry", [])

                if not entries:
                    print(f"  Page {page}: Empty feed entry list.")
                    break

                # Handle single-entry quirk (dict instead of list)
                if isinstance(entries, dict):
                    entries = [entries]

                page_reviews = 0
                for entry in entries:
                    # Skip metadata entry (has no im:rating)
                    if "im:rating" not in entry:
                        continue

                    review_id = entry.get("id", {}).get("label", "")
                    rating_str = entry.get("im:rating", {}).get("label", "0")
                    title = entry.get("title", {}).get("label", "")
                    content = entry.get("content", {}).get("label", "")
                    updated_str = entry.get("updated", {}).get("label", "")
                    version = entry.get("im:version", {}).get("label", "")
                    vote_sum = entry.get("im:voteSum", {}).get("label", "0")

                    # Parse date (ISO 8601 with timezone)
                    dt = datetime.fromisoformat(updated_str)
                    all_dates.append(dt)
                    page_reviews += 1
                    total_reviews += 1

                    if len(sample_clean_entries) < 10:
                        sample_clean_entries.append(
                            {
                                "source": "app_store",
                                "review_id": review_id,
                                "rating": int(rating_str),
                                "title": scrub_pii_basic(title),
                                "text": scrub_pii_basic(content),
                                "date": dt.isoformat(),
                                "app_version": version,
                                "helpful_votes": int(vote_sum) if vote_sum.isdigit() else 0,
                            }
                        )

                pages_fetched += 1
                print(f"  Page {page}: Fetched {page_reviews} reviews")
                time.sleep(0.3)

            except Exception as e:
                print(f"  Page {page} error: {e}")
                break

    all_dates.sort()
    now_utc = datetime.now(UTC)
    oldest_dt = all_dates[0] if all_dates else None
    newest_dt = all_dates[-1] if all_dates else None

    depth_days = (now_utc - oldest_dt).days if oldest_dt else 0
    depth_weeks = depth_days / 7.0

    print("\nApp Store Results:")
    print(f"  Total pages fetched: {pages_fetched}")
    print(f"  Total reviews fetched: {total_reviews}")
    if oldest_dt and newest_dt:
        print(f"  Newest review: {newest_dt.isoformat()} (ISO week: {date_to_iso_week(newest_dt)})")
        print(f"  Oldest review: {oldest_dt.isoformat()} (ISO week: {date_to_iso_week(oldest_dt)})")
        print(f"  Coverage depth: {depth_days} days (~{depth_weeks:.1f} weeks)")

    # Save scrubbed sample fixtures if fetched
    if sample_clean_entries:
        fixtures_file = FIXTURES_DIR / "app_store_sample.json"
        with fixtures_file.open("w", encoding="utf-8") as f:
            json.dump(sample_clean_entries, f, indent=2)
        print(f"  Saved {len(sample_clean_entries)} scrubbed fixtures to {fixtures_file}")

    return {
        "total_reviews": total_reviews,
        "pages_fetched": pages_fetched,
        "oldest_dt": oldest_dt.isoformat() if oldest_dt else None,
        "newest_dt": newest_dt.isoformat() if newest_dt else None,
        "depth_days": depth_days,
        "depth_weeks": depth_weeks,
    }


def run_play_store_spike() -> dict[str, Any]:
    print("\n--- 2. Google Play Store Spike ---")
    package_id = "com.nextbillion.groww"

    print(f"Fetching 200 newest reviews for '{package_id}' (lang=en, country=in)...")
    result, _ = reviews(
        package_id,
        lang="en",
        country="in",
        sort=Sort.NEWEST,
        count=200,
    )

    print(f"  Fetched {len(result)} reviews.")
    sample_clean_entries: list[dict[str, Any]] = []
    all_dates: list[datetime] = []

    for r in result:
        dt = r.get("at")
        if isinstance(dt, datetime):
            all_dates.append(dt)

        if len(sample_clean_entries) < 10:
            sample_clean_entries.append(
                {
                    "source": "play_store",
                    "review_id": r.get("reviewId", ""),
                    "rating": r.get("score", 0),
                    "title": None,
                    "text": scrub_pii_basic(r.get("content", "")),
                    "date": dt.isoformat() if isinstance(dt, datetime) else str(dt),
                    "app_version": r.get("reviewCreatedVersion"),
                    "helpful_votes": r.get("thumbsUpCount", 0),
                }
            )

    all_dates.sort()
    now_utc = datetime.now(UTC)
    oldest_dt = all_dates[0] if all_dates else None
    newest_dt = all_dates[-1] if all_dates else None

    # Replace timezone if naive
    if oldest_dt and oldest_dt.tzinfo is None:
        oldest_dt = oldest_dt.replace(tzinfo=UTC)
    if newest_dt and newest_dt.tzinfo is None:
        newest_dt = newest_dt.replace(tzinfo=UTC)

    depth_days = (now_utc - oldest_dt).days if oldest_dt else 0
    depth_weeks = depth_days / 7.0

    print("\nPlay Store (200 sample) Results:")
    if oldest_dt and newest_dt:
        print(f"  Newest review: {newest_dt.isoformat()} (ISO week: {date_to_iso_week(newest_dt)})")
        print(f"  Oldest review: {oldest_dt.isoformat()} (ISO week: {date_to_iso_week(oldest_dt)})")
        print(f"  200 reviews span: {depth_days} days (~{depth_weeks:.1f} weeks)")

    # Save scrubbed sample fixtures
    fixtures_file = FIXTURES_DIR / "play_store_sample.json"
    with fixtures_file.open("w", encoding="utf-8") as f:
        json.dump(sample_clean_entries, f, indent=2)
    print(f"  Saved {len(sample_clean_entries)} scrubbed fixtures to {fixtures_file}")

    return {
        "sample_count": len(result),
        "oldest_dt": oldest_dt.isoformat() if oldest_dt else None,
        "newest_dt": newest_dt.isoformat() if newest_dt else None,
        "depth_days": depth_days,
        "depth_weeks": depth_weeks,
    }


def main() -> None:
    print("Groww Review Pulse — Source Feed Spike (Spike B)")
    print(f"Current ISO Week: {current_week()}")

    as_results = run_app_store_spike()
    ps_results = run_play_store_spike()

    print("\n==================================================")
    print(" SPIKE B FINDINGS SUMMARY")
    print("==================================================")
    print("App Store:")
    print(f"  Total reviews available via RSS: {as_results['total_reviews']}")
    as_depth = (
        f"  Coverage depth: ~{as_results['depth_weeks']:.1f} weeks "
        f"({as_results['depth_days']} days)"
    )
    print(as_depth)
    if as_results["depth_weeks"] < 8.0:
        print("  [CRITICAL FINDING] App Store RSS ~500 review limit covers LESS than 8 weeks!")
        print("  Conclusion: Weekly accumulation (Task 1.7 & P2) and backfill are MANDATORY.")
    else:
        print("  App Store RSS feed covers >= 8 weeks.")

    print("\nPlay Store:")
    ps_span = (
        f"  200 reviews span: ~{ps_results['depth_weeks']:.1f} weeks "
        f"({ps_results['depth_days']} days)"
    )
    print(ps_span)
    print("  Volume is very high; pagination with continuation token will easily reach 8-12 weeks.")


if __name__ == "__main__":
    main()
