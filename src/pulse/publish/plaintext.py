"""Plain-text rendering of a Pulse for Google Docs (no Markdown syntax).

The Docs MCP tool only accepts plain text, so structure comes from spacing, rules and
unicode glyphs instead of markup. The same text is the fallback email body.
"""

from __future__ import annotations

from pulse.models import Pulse

MAX_DOC_CHARS = 50_000  # google_docs_append_text limit

_SOURCE_LABEL = {"play_store": "Play Store", "app_store": "App Store"}
_RULE = "─" * 44


def _section(title: str) -> list[str]:
    return ["", _RULE, title, _RULE, ""]


def render_plain(pulse: Pulse) -> str:
    theme_name = {t.theme_id: t.name for t in pulse.top_themes}
    play = pulse.reviews_by_source.get("play_store", 0)
    app = pulse.reviews_by_source.get("app_store", 0)
    lines: list[str] = [
        "WEEK SNAPSHOT",
        "",
        f"Window:  {pulse.window_start} to {pulse.window_end}",
        f"Reviews:  {pulse.total_reviews:,}   ·   Average rating:  {pulse.avg_rating:.2f} / 5",
        f"Play Store:  {play:,} reviews ({pulse.avg_rating_by_source.get('play_store', 0):.2f} stars)",
        f"App Store:  {app:,} reviews ({pulse.avg_rating_by_source.get('app_store', 0):.2f} stars)",
    ]

    lines += _section("TOP THEMES")
    for i, t in enumerate(pulse.top_themes, start=1):
        stats = [
            f"{t.share * 100:.1f}% of reviews",
            f"{t.low_rating_share * 100:.1f}% rated 1-2 stars",
        ]
        if t.wow_delta is not None:
            stats.append(f"{t.wow_delta:+.1f} pts week over week")
        lines += [
            f"{i}.  {t.name}",
            f"     {'  ·  '.join(stats)}",
            f"     Play Store {t.by_source.get('play_store', 0)}  ·  App Store {t.by_source.get('app_store', 0)}",
            f"     {t.definition}",
            "",
        ]

    lines += _section("WHAT USERS SAID")
    for q in pulse.quotes:
        lines += [
            f"“{q.text}”",
            f"     — {_SOURCE_LABEL[q.source]}, {q.rating} stars, {q.month}"
            f"  ·  {theme_name.get(q.theme_id, q.theme_id)}",
            "",
        ]

    lines += _section("ACTION IDEAS")
    for i, a in enumerate(pulse.actions, start=1):
        lines += [
            f"{i}.  {a.title}",
            f"     {a.rationale}",
            f"     Owner: {a.owner_hint}  ·  Theme: {theme_name.get(a.theme_id, a.theme_id)}",
            "",
        ]

    text = "\n".join(lines).rstrip() + "\n"
    if len(text) > MAX_DOC_CHARS:
        raise ValueError(
            f"Plain-text pulse is {len(text)} chars; the Docs limit is {MAX_DOC_CHARS}."
        )
    return text
