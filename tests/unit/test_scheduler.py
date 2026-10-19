from datetime import UTC, datetime

from pulse.scheduler import due


def test_due_only_on_monday_after_hour():
    assert due(datetime(2026, 10, 19, 4, 0, tzinfo=UTC))  # Monday 04:00
    assert not due(datetime(2026, 10, 19, 3, 0, tzinfo=UTC))  # too early
    assert not due(datetime(2026, 10, 20, 4, 0, tzinfo=UTC))  # Tuesday
