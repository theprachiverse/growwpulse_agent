"""Phase 1 ingestion tests."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from pulse.config import AppStoreConfig, PlayStoreConfig
from pulse.ingest.appstore_source import (
    FEED_URL,
    AppStoreSource,
    extract_entries,
    map_app_review,
)
from pulse.ingest.base import ReviewSource
from pulse.ingest.fetcher import AllSourcesFailedError, fetch_all, missing_snapshot_weeks
from pulse.ingest.file_source import FileSource
from pulse.ingest.normalizer import dedup, filter_window
from pulse.ingest.playstore_source import PlayStoreSource, map_play_review
from pulse.models import RawReview

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
WEEK = "2026-W42"
FIXTURES = Path(__file__).parent.parent / "fixtures"
FORBIDDEN = {"userName", "userImage", "replyContent", "repliedAt", "author"}


def play_item(i: int, at: datetime, content: str | None = None) -> dict:
    return {
        "reviewId": f"p{i}",
        "userName": "Secret Name",
        "userImage": "http://img",
        "content": content if content is not None else f"play review number {i}",
        "score": 3,
        "thumbsUpCount": 2,
        "reviewCreatedVersion": "18.2",
        "at": at.replace(tzinfo=None),
        "replyContent": "thanks",
        "repliedAt": at,
    }


def app_entry(i: int, updated: datetime, votes: bool = True) -> dict:
    e = {
        "author": {"name": {"label": "Real Person"}, "uri": {"label": "http://x"}},
        "updated": {"label": updated.isoformat()},
        "im:rating": {"label": "4"},
        "im:version": {"label": "18.20.0"},
        "id": {"label": f"a{i}"},
        "title": {"label": f"title {i}"},
        "content": {"label": f"app review text {i}", "attributes": {"type": "text"}},
        "link": {"attributes": {"href": "http://l"}},
    }
    if votes:
        e["im:voteSum"] = {"label": "3"}
    return e


# ---------- mappers ----------
def test_play_mapper_discards_user_fields():
    r = map_play_review(play_item(1, NOW))
    assert r is not None
    assert r.source == "play_store" and r.title is None
    assert r.date.tzinfo is not None and r.helpful_votes == 2
    dumped = json.dumps(r.model_dump(mode="json"))
    assert not any(k in dumped for k in FORBIDDEN) and "Secret Name" not in dumped
    assert set(type(r).model_fields) == set(RawReview.model_fields)


def test_play_mapper_skips_incomplete():
    assert map_play_review({"reviewId": "x"}) is None


def test_app_mapper_variants():
    meta = {"im:name": {"label": "Groww"}, "id": {"label": "1"}}
    assert map_app_review(meta) is None
    r = map_app_review(app_entry(1, NOW, votes=False))
    assert r is not None and r.helpful_votes == 0 and r.title == "title 1"
    assert "Real Person" not in r.model_dump_json()
    assert map_app_review(app_entry(2, NOW)).helpful_votes == 3  # type: ignore[union-attr]


def test_single_entry_page_normalised_to_list():
    assert extract_entries({"feed": {"entry": app_entry(1, NOW)}}) == [app_entry(1, NOW)]
    assert extract_entries({"feed": {}}) == []


# ---------- play source ----------
def test_play_source_paginates_and_stops_at_window():
    start = NOW - timedelta(days=5)
    pages = [
        ([play_item(i, NOW - timedelta(days=i)) for i in range(3)], "tok1"),
        ([play_item(10 + i, NOW - timedelta(days=3 + i)) for i in range(5)], "tok2"),
        ([play_item(99, NOW)], None),
    ]
    calls: list = []

    def fn(**kw):
        calls.append(kw["continuation_token"])
        return pages[len(calls) - 1]

    src = PlayStoreSource(PlayStoreConfig(), reviews_fn=fn, sleep=lambda _s: None)
    out = src.fetch(start)
    assert calls == [None, "tok1"]  # stopped when a review fell before window
    assert all(r.date >= start for r in out) and len(out) == 3 + 3  # day 5 == start, inclusive


def test_play_source_respects_max_reviews_and_retries():
    attempts = {"n": 0}

    def fn(**kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("throttled")
        return [play_item(i, NOW) for i in range(kw["count"])], "more"

    cfg = PlayStoreConfig(max_reviews=7, page_size=5)
    out = PlayStoreSource(cfg, reviews_fn=fn, sleep=lambda _s: None).fetch(NOW - timedelta(days=1))
    assert len(out) == 7 and attempts["n"] == 3


# ---------- app store source ----------
def url(page: int) -> str:
    return FEED_URL.format(country="in", page=page, app_id="1404871703")


@respx.mock
def test_app_store_pagination_stops_on_empty_and_skips_metadata():
    meta = {"im:name": {"label": "Groww"}, "id": {"label": "1"}}
    p1 = {"feed": {"entry": [meta, app_entry(1, NOW), app_entry(2, NOW)]}}
    p2 = {"feed": {"entry": app_entry(3, NOW)}}  # single dict entry
    p3 = {"feed": {}}
    for i, p in enumerate([p1, p2, p3], 1):
        respx.get(url(i)).mock(return_value=httpx.Response(200, json=p))
    p4 = respx.get(url(4)).mock(return_value=httpx.Response(200, json=p1))
    out = AppStoreSource(AppStoreConfig(), sleep=lambda _s: None).fetch(NOW - timedelta(days=30))
    assert [r.review_id for r in out] == ["a1", "a2", "a3"]
    assert not p4.called


@respx.mock
def test_app_store_stops_at_window_start():
    old = NOW - timedelta(days=40)
    payload = {"feed": {"entry": [app_entry(1, NOW), app_entry(2, old)]}}
    respx.get(url(1)).mock(return_value=httpx.Response(200, json=payload))
    p2 = respx.get(url(2)).mock(return_value=httpx.Response(200, json={"feed": {}}))
    out = AppStoreSource(AppStoreConfig(), sleep=lambda _s: None).fetch(NOW - timedelta(days=10))
    assert [r.review_id for r in out] == ["a1"] and not p2.called


@respx.mock
def test_app_store_5xx_retries():
    route = respx.get(url(1)).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(502),
            httpx.Response(200, json={"feed": {"entry": [app_entry(1, NOW)]}}),
        ]
    )
    respx.get(url(2)).mock(return_value=httpx.Response(200, json={"feed": {}}))
    sleeps: list[float] = []
    out = AppStoreSource(AppStoreConfig(), sleep=sleeps.append).fetch(NOW - timedelta(days=1))
    assert route.call_count == 3 and len(out) == 1 and sleeps == [1.0, 2.0]


@respx.mock
def test_app_store_persistent_5xx_raises():
    respx.get(url(1)).mock(return_value=httpx.Response(500))
    with pytest.raises(Exception):  # noqa: B017
        AppStoreSource(AppStoreConfig(), sleep=lambda _s: None).fetch(NOW)


# ---------- normalizer ----------
def raw(i: str, text: str, source="play_store", date=NOW) -> RawReview:
    return RawReview(source=source, review_id=i, rating=3, text=text, date=date)


def test_window_filter_and_utc():
    from datetime import date as d

    ist = datetime(2026, 10, 12, 1, 0, tzinfo=UTC).astimezone(timezone_ist())
    rs = [raw("in", "x", date=ist), raw("old", "y", date=NOW - timedelta(days=100))]
    out = filter_window(rs, d(2026, 10, 1), d(2026, 10, 18))
    assert [r.review_id for r in out] == ["in"] and out[0].date.utcoffset() == timedelta(0)


def timezone_ist():
    from datetime import timezone

    return timezone(timedelta(hours=5, minutes=30))


def test_dedup_by_id_and_text():
    rs = [
        raw("1", "same text"),
        raw("1", "different"),  # dup id
        raw("2", "Same  Text"),  # repost
        raw("3", "same text", source="app_store"),  # other source kept
        raw("4", ""),
        raw("5", ""),  # empty texts not text-deduped
    ]
    assert [(r.source, r.review_id) for r in dedup(rs)] == [
        ("play_store", "1"),
        ("app_store", "3"),
        ("play_store", "4"),
        ("play_store", "5"),
    ]


# ---------- file source ----------
def test_file_source_json_and_csv(tmp_path: Path):
    data = json.loads((FIXTURES / "play_store_sample.json").read_text(encoding="utf-8"))
    jp = tmp_path / "x.json"
    jp.write_text(json.dumps(data), encoding="utf-8")
    out = FileSource(jp, "play_store").fetch(datetime(2026, 1, 1, tzinfo=UTC))
    assert len(out) == len(data) and all(r.source == "play_store" for r in out)

    cp = tmp_path / "x.csv"
    cp.write_text(
        "id,stars,body,when,author\n7,5,great app,2026-10-10T00:00:00Z,John\n", encoding="utf-8"
    )
    out = FileSource(
        cp, "app_store", {"review_id": "id", "rating": "stars", "text": "body", "date": "when"}
    ).fetch(datetime(2026, 1, 1, tzinfo=UTC))
    assert len(out) == 1 and out[0].rating == 5 and "John" not in out[0].model_dump_json()


# ---------- fetcher ----------
class FakeSource(ReviewSource):
    def __init__(self, name, reviews=None, error=None):
        self.name = name
        self._reviews, self._error = reviews or [], error

    def fetch(self, window_start):
        if self._error:
            raise self._error
        return self._reviews


def test_partial_failure_keeps_other_source():
    play = FakeSource("play_store", [raw("1", "hello world", date=NOW)])
    app = FakeSource("app_store", error=RuntimeError("boom"))
    res = fetch_all([play, app], WEEK, 10)
    assert len(res.reviews) == 1
    assert "boom" in res.source_errors["app_store"]
    assert "play_store" not in res.source_errors
    assert res.coverage_start["play_store"] == NOW.date()


def test_all_sources_fail_raises():
    sources = [
        FakeSource("play_store", error=ValueError("a")),
        FakeSource("app_store", error=ValueError("b")),
    ]
    with pytest.raises(AllSourcesFailedError):
        fetch_all(sources, WEEK, 10)


def test_fetch_all_filters_window_and_counts():
    old = NOW - timedelta(days=200)
    res = fetch_all(
        [
            FakeSource("play_store", [raw("1", "a b c", date=NOW), raw("2", "old one", date=old)]),
            FakeSource("app_store", [raw("3", "d e f", "app_store", NOW)]),
        ],
        WEEK,
        10,
    )
    assert res.counts_by_source() == {"play_store": 1, "app_store": 1}
    assert res.counts_by_week() == {WEEK: {"play_store": 1, "app_store": 1}}


def test_missing_snapshot_weeks():
    from datetime import date

    weeks = missing_snapshot_weeks(date(2026, 9, 1), WEEK, 10)
    assert weeks[0] == "2026-W33" and weeks[-1] == "2026-W35"
    assert missing_snapshot_weeks(date(2026, 7, 1), WEEK, 10) == []


def test_ec_ing_08_dedup_keeps_newest_version_of_edited_review():
    old = raw("same", "old text about withdrawals", date=NOW - timedelta(days=5))
    new = raw("same", "edited text about withdrawals", date=NOW)
    out = dedup([old, raw("other", "different"), new])
    assert [r.review_id for r in out] == ["same", "other"]  # first-seen order kept
    assert out[0].text == "edited text about withdrawals"

    # newest wins regardless of input order
    assert dedup([new, old])[0].text == "edited text about withdrawals"
