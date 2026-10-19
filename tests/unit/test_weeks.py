"""Unit tests for ISO week helpers (Task 0.4)."""

from datetime import date

import pytest

from pulse.utils.weeks import (
    current_week,
    date_to_iso_week,
    format_week,
    parse_week,
    week_end_date,
    week_start_date,
    window_bounds,
)


def test_current_week_format() -> None:
    cw = current_week()
    year, week = parse_week(cw)
    assert 2020 <= year <= 2030
    assert 1 <= week <= 53


def test_parse_and_format_week() -> None:
    assert parse_week("2026-W42") == (2026, 42)
    assert format_week(2026, 5) == "2026-W05"

    with pytest.raises(ValueError):
        parse_week("invalid-week")

    with pytest.raises(ValueError):
        parse_week("2026-W55")  # > 53


def test_week_start_and_end() -> None:
    # 2026-W42:
    # Oct 12, 2026 is Monday (day 1)
    # Oct 18, 2026 is Sunday (day 7)
    start = week_start_date("2026-W42")
    end = week_end_date("2026-W42")

    assert start == date(2026, 10, 12)
    assert end == date(2026, 10, 18)
    assert (end - start).days == 6


def test_window_bounds_10_weeks() -> None:
    # 10 weeks ending on 2026-W42:
    # End date: Oct 18, 2026 (Sunday of W42)
    # Start date: Monday 9 weeks prior -> Aug 10, 2026
    # Total days inclusive: 70 days (10 weeks * 7 days)
    start_d, end_d = window_bounds("2026-W42", 10)
    assert end_d == date(2026, 10, 18)
    assert start_d == date(2026, 8, 10)
    assert (end_d - start_d).days + 1 == 70


def test_date_to_iso_week() -> None:
    assert date_to_iso_week(date(2026, 10, 14)) == "2026-W42"
    assert date_to_iso_week(date(2026, 10, 18)) == "2026-W42"
    assert date_to_iso_week(date(2026, 10, 19)) == "2026-W43"


def test_last_completed_week_is_previous_week() -> None:
    from pulse.utils.weeks import current_week, last_completed_week

    cur_y, cur_w = parse_week(current_week())
    last_y, last_w = parse_week(last_completed_week())
    assert week_end_date(last_w and f"{last_y}-W{last_w:02d}") < week_start_date(
        f"{cur_y}-W{cur_w:02d}"
    )


def test_parse_week_requires_two_digit_week() -> None:
    for bad in ("2026-W4", "2026W42", "2026-w42"):
        with pytest.raises(ValueError):
            parse_week(bad)


def test_ec_ops_02_year_boundary_has_week_53() -> None:
    from pulse.theming.pipeline import get_previous_week_string

    assert get_previous_week_string("2027-W01") == "2026-W53"
    assert get_previous_week_string("2026-W42") == "2026-W41"
