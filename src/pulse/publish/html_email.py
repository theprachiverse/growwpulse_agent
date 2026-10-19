"""Styled HTML email for the weekly pulse (Groww design tokens, inline CSS for mail clients).

Table-based layout with inline styles only: Gmail and Outlook strip <style> blocks and ignore
flexbox/grid. Every dynamic string is HTML-escaped.
"""

from __future__ import annotations

from html import escape

from pulse.models import Pulse

# Tokens from stitch_groww_review_pulse_dashboard/DESIGN.md
INK, MUTED, SLATE = "#1E2238", "#8C93A8", "#44475B"
LINE, CANVAS, SURFACE = "#E5E9F0", "#F8F9FB", "#FFFFFF"
GREEN, GREEN_INK, GREEN_SOFT = "#00B386", "#006C4F", "#E6F7F2"
AMBER, AMBER_SOFT = "#D97706", "#FEF3E2"
RED, RED_SOFT = "#DC2626", "#FDECEC"
FONT = "Inter,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

_SOURCE_LABEL = {"play_store": "Play Store", "app_store": "App Store"}


def _e(x: object) -> str:
    return escape(str(x), quote=True)


def _chip(text: str, fg: str = SLATE, bg: str = CANVAS, border: str = LINE) -> str:
    return (
        f'<span style="display:inline-block;margin:0 6px 6px 0;padding:3px 10px;border-radius:99px;'
        f'background:{bg};border:1px solid {border};color:{fg};font-size:12px;font-weight:500;">{_e(text)}</span>'
    )


def _label(text: str) -> str:
    return (
        f'<div style="font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
        f'color:{MUTED};margin:0 0 12px;">{_e(text)}</div>'
    )


def _section(title: str, inner: str) -> str:
    return f'<tr><td style="padding:24px 28px 4px;">{_label(title)}{inner}</td></tr>'


def _stat(label: str, value: str, note: str = "") -> str:
    return (
        f'<td width="25%" valign="top" style="padding:14px 12px;border:1px solid {LINE};background:{SURFACE};'
        f'border-radius:10px;">'
        f'<div style="font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};">{_e(label)}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{INK};line-height:1.25;margin-top:2px;">{_e(value)}</div>'
        f'<div style="font-size:11px;color:{MUTED};margin-top:1px;">{_e(note) or "&nbsp;"}</div></td>'
    )


def render_html(pulse: Pulse, doc_url: str | None = None) -> str:
    theme_name = {t.theme_id: t.name for t in pulse.top_themes}
    play = pulse.reviews_by_source.get("play_store", 0)
    app = pulse.reviews_by_source.get("app_store", 0)

    stats = (
        '<table role="presentation" width="100%" cellspacing="8" cellpadding="0" style="margin:0 -8px;"><tr>'
        + _stat("Reviews", f"{pulse.total_reviews:,}", "in window")
        + _stat("Avg rating", f"{pulse.avg_rating:.2f} ★", "out of 5")
        + _stat(
            "Play Store",
            f"{play:,}",
            f"{pulse.avg_rating_by_source.get('play_store', 0):.2f} ★ avg",
        )
        + _stat(
            "App Store", f"{app:,}", f"{pulse.avg_rating_by_source.get('app_store', 0):.2f} ★ avg"
        )
        + "</tr></table>"
    )

    themes = ""
    for i, t in enumerate(pulse.top_themes, start=1):
        low = t.low_rating_share
        low_chip = _chip(
            f"{low * 100:.1f}% rated 1–2★",
            *(
                (RED, RED_SOFT, RED_SOFT)
                if low > 0.6
                else (AMBER, AMBER_SOFT, AMBER_SOFT)
                if low > 0.4
                else ()
            ),
        )
        chips = (
            _chip(f"{t.share * 100:.1f}% of reviews", GREEN_INK, GREEN_SOFT, GREEN_SOFT) + low_chip
        )
        if t.wow_delta is not None:
            up = t.wow_delta > 0
            chips += _chip(
                f"{t.wow_delta:+.1f} pts WoW",
                *((RED, RED_SOFT, RED_SOFT) if up else (GREEN_INK, GREEN_SOFT, GREEN_SOFT)),
            )
        chips += _chip(f"Play {t.by_source.get('play_store', 0)}") + _chip(
            f"iOS {t.by_source.get('app_store', 0)}"
        )
        themes += (
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin:0 0 12px;border:1px solid {LINE};border-radius:12px;background:{SURFACE};"><tr>'
            f'<td width="44" valign="top" style="padding:16px 0 16px 16px;">'
            f'<div style="width:28px;height:28px;border-radius:50%;background:{GREEN};color:#fff;font-weight:700;'
            f'font-size:14px;line-height:28px;text-align:center;">{i}</div></td>'
            f'<td valign="top" style="padding:16px 16px 10px 8px;">'
            f'<div style="font-size:16px;font-weight:700;color:{INK};">{_e(t.name)}</div>'
            f'<div style="font-size:14px;line-height:21px;color:{SLATE};margin:4px 0 10px;">{_e(t.definition)}</div>'
            f"{chips}</td></tr></table>"
        )

    quotes = ""
    for q in pulse.quotes:
        quotes += (
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 12px;">'
            f'<tr><td width="4" style="background:{GREEN};border-radius:2px;"></td>'
            f'<td style="padding:6px 0 6px 16px;">'
            f'<div style="font-size:15px;line-height:24px;color:{INK};font-style:italic;">“{_e(q.text)}”</div>'
            f'<div style="font-size:12px;color:{MUTED};margin-top:6px;">'
            f"{q.rating}★ · {_e(_SOURCE_LABEL[q.source])} · {_e(q.month)} · "
            f"{_e(theme_name.get(q.theme_id, q.theme_id))}</div></td></tr></table>"
        )

    actions = ""
    for i, a in enumerate(pulse.actions, start=1):
        actions += (
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin:0 0 10px;border:1px solid {LINE};border-radius:12px;background:{SURFACE};"><tr>'
            f'<td style="padding:14px 16px;">'
            f'<div style="font-size:15px;font-weight:700;color:{INK};">{i}. {_e(a.title)}</div>'
            f'<div style="font-size:14px;line-height:21px;color:{SLATE};margin:4px 0 8px;">{_e(a.rationale)}</div>'
            f"{_chip('Owner: ' + a.owner_hint, GREEN_INK, GREEN_SOFT, GREEN_SOFT)}"
            f"{_chip(theme_name.get(a.theme_id, a.theme_id))}</td></tr></table>"
        )

    cta = ""
    if doc_url:
        u = _e(doc_url)
        cta = (
            f'<tr><td style="padding:20px 28px 8px;" align="center">'
            f'<a href="{u}" style="display:inline-block;background:{GREEN};color:#fff;text-decoration:none;'
            f'font-weight:600;font-size:14px;padding:11px 22px;border-radius:8px;">Open full history in Google Docs</a>'
            f'<div style="font-size:11px;color:{MUTED};margin-top:8px;word-break:break-all;">{u}</div></td></tr>'
        )

    return (
        f'<!doctype html><html><body style="margin:0;padding:0;background:{CANVAS};">'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{CANVAS};">'
        f'<tr><td align="center" style="padding:24px 12px;">'
        f'<table role="presentation" width="640" cellspacing="0" cellpadding="0" '
        f'style="width:100%;max-width:640px;background:{SURFACE};border:1px solid {LINE};border-radius:16px;'
        f'font-family:{FONT};color:{INK};">'
        # header band
        f'<tr><td style="background:{GREEN};border-radius:16px 16px 0 0;padding:26px 28px;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#CFF5E8;">Groww Pulse · Weekly digest</div>'
        f'<div style="font-size:26px;font-weight:700;color:#fff;letter-spacing:-.02em;margin-top:4px;">Weekly Review Pulse</div>'
        f'<div style="font-size:14px;color:#E6FBF4;margin-top:4px;">{_e(pulse.week)} · {_e(pulse.window_start)} to {_e(pulse.window_end)}</div>'
        f"</td></tr>"
        f'<tr><td style="padding:24px 28px 0;">{stats}</td></tr>'
        + _section("Top themes", themes)
        + _section("What users said", quotes)
        + _section("Action ideas", actions)
        + cta
        + f'<tr><td style="padding:20px 28px 26px;border-top:1px solid {LINE};margin-top:16px;'
        f'font-size:12px;color:{MUTED};line-height:18px;">'
        f"Generated automatically from public Google Play and Apple App Store reviews. "
        f"Personal information is scrubbed before analysis; quotes are verbatim and anonymised.</td></tr>"
        f"</table></td></tr></table></body></html>"
    )
