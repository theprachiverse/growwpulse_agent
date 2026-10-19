"""ISO week calculation utilities for window bounds and partitioning.

The Groww review pulse operates on rolling windows of N weeks (default 10,
allowed 8..12) anchored to a specific ISO week (e.g. 2026-W42).
"""

import re
from datetime import UTC, date, datetime, timedelta

WEEK_REGEX = re.compile(r"^(\d{4})-W(\d{2})$")


def current_week() -> str:
    """Returns the current ISO week string in UTC, e.g. '2026-W42'."""
    now = datetime.now(UTC).date()
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def last_completed_week() -> str:
    """Returns the most recent fully finished ISO week (UTC), e.g. on Monday 2026-10-19 -> '2026-W42'.

    Use this for scheduled runs: the in-progress week has only a few hours of data.
    """
    last_sunday_ish = datetime.now(UTC).date() - timedelta(days=7)
    iso = last_sunday_ish.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def parse_week(week_str: str) -> tuple[int, int]:
    """Parses an ISO week string like '2026-W42' into (year, week_num)."""
    match = WEEK_REGEX.match(week_str.strip())
    if not match:
        raise ValueError(
            f"Invalid ISO week format '{week_str}'. Expected 'YYYY-Www' with a two-digit week (e.g. 2026-W42)"
        )
    year = int(match.group(1))
    week = int(match.group(2))
    if not (1 <= week <= 53):
        raise ValueError(f"Invalid ISO week number {week} in '{week_str}'")
    return year, week


def format_week(year: int, week: int) -> str:
    """Formats year and week into standard 'YYYY-Www'."""
    return f"{year}-W{week:02d}"


def date_to_iso_week(d: date | datetime) -> str:
    """Converts a date or datetime to an ISO week string."""
    if isinstance(d, datetime):
        d = d.date()
    iso = d.isocalendar()
    return format_week(iso.year, iso.week)


def week_end_date(week_str: str) -> date:
    """Returns the Sunday (end date) of the specified ISO week."""
    year, week = parse_week(week_str)
    # date.fromisocalendar(year, week, 7) is Sunday
    return date.fromisocalendar(year, week, 7)


def week_start_date(week_str: str) -> date:
    """Returns the Monday (start date) of the specified ISO week."""
    year, week = parse_week(week_str)
    # date.fromisocalendar(year, week, 1) is Monday
    return date.fromisocalendar(year, week, 1)


def window_bounds(week_str: str, n_weeks: int) -> tuple[date, date]:
    """Calculates the inclusive date range for an N-week window ending on the target week.

    Args:
        week_str: ISO week string representing the end of the window, e.g. '2026-W42'.
        n_weeks: Number of weeks in the rolling window (e.g. 10).

    Returns:
        (window_start, window_end) as inclusive dates.
        window_end is Sunday of week_str.
        window_start is Monday of the week (n_weeks - 1) prior.
    """
    if n_weeks <= 0:
        raise ValueError(f"n_weeks must be positive, got {n_weeks}")

    end_date = week_end_date(week_str)
    # The start is n_weeks * 7 days minus 6 days (to start on Monday)
    start_date = end_date - timedelta(days=(n_weeks * 7) - 1)
    return start_date, end_date


def list_all_completed_weeks(year: int | None = None) -> list[str]:
    """Returns all completed ISO weeks for the given year (or current year) in descending order.

    e.g. ['2026-W42', '2026-W41', ..., '2026-W01']
    """
    last_week_str = last_completed_week()
    last_year, last_w = parse_week(last_week_str)

    target_year = year if year is not None else last_year
    max_w = last_w if target_year == last_year else 52

    return [format_week(target_year, w) for w in range(max_w, 0, -1)]


def format_week_human(week_str: str) -> str:
    """Formats an ISO week into a friendly label like 'August W2 (Aug 10 - Aug 16)'."""
    s = week_start_date(week_str)
    e = week_end_date(week_str)
    thurs = s + timedelta(days=3)
    w_of_m = (thurs.day - 1) // 7 + 1
    m_name = thurs.strftime("%B")
    s_str = s.strftime("%b %d")
    e_str = e.strftime("%b %d")
    return f"{m_name} W{w_of_m} ({s_str} - {e_str})"
