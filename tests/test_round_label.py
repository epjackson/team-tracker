"""Unit tests for fixture_round_label — no app context needed, pure function."""

from datetime import date, timedelta

from app.models import fixture_round_label

SEASON_START = date(2026, 4, 4)  # a Saturday


def _saturday(weeks_after_start):
    return SEASON_START + timedelta(weeks=weeks_after_start)


def test_no_start_date_returns_none():
    """No league start date means round labels can't be computed."""
    assert fixture_round_label(date(2026, 4, 4), None) is None


def test_date_before_season_start_returns_none():
    """A fixture before the season begins has no round label."""
    assert fixture_round_label(SEASON_START - timedelta(days=1), SEASON_START) is None


def test_no_catchup_weeks_gives_consecutive_rounds():
    """Cleveland Autumn League case: no catch-up weeks, rounds run back to back."""
    for week in range(6):
        label = fixture_round_label(_saturday(week), SEASON_START, [])
        assert label == f"Round {week + 1}"


def test_no_catchup_weeks_none_argument():
    """catchup_week_starts=None behaves the same as an empty list."""
    assert fixture_round_label(_saturday(2), SEASON_START, None) == "Round 3"


def test_single_catchup_week():
    """One catch-up week: rounds skip over it but keep incrementing after."""
    catchup = [_saturday(4)]
    assert fixture_round_label(_saturday(0), SEASON_START, catchup) == "Round 1"
    assert fixture_round_label(_saturday(3), SEASON_START, catchup) == "Round 4"
    assert fixture_round_label(_saturday(4), SEASON_START, catchup) == "Catch-up Week"
    assert fixture_round_label(_saturday(5), SEASON_START, catchup) == "Round 5"
    assert fixture_round_label(_saturday(6), SEASON_START, catchup) == "Round 6"


def test_multiple_irregularly_spaced_catchup_weeks():
    """Cleveland Summer League case: several catch-up weeks at irregular spacing."""
    catchup = [_saturday(2), _saturday(3), _saturday(7)]
    assert fixture_round_label(_saturday(0), SEASON_START, catchup) == "Round 1"
    assert fixture_round_label(_saturday(1), SEASON_START, catchup) == "Round 2"
    assert fixture_round_label(_saturday(2), SEASON_START, catchup) == "Catch-up Week"
    assert fixture_round_label(_saturday(3), SEASON_START, catchup) == "Catch-up Week"
    assert fixture_round_label(_saturday(4), SEASON_START, catchup) == "Round 3"
    assert fixture_round_label(_saturday(5), SEASON_START, catchup) == "Round 4"
    assert fixture_round_label(_saturday(6), SEASON_START, catchup) == "Round 5"
    assert fixture_round_label(_saturday(7), SEASON_START, catchup) == "Catch-up Week"
    assert fixture_round_label(_saturday(8), SEASON_START, catchup) == "Round 6"


def test_fixture_mid_week_uses_week_start():
    """A fixture any day within a round's week still gets that round's label."""
    mid_week_date = _saturday(2) + timedelta(days=3)
    assert fixture_round_label(mid_week_date, SEASON_START, []) == "Round 3"
