"""Unit tests for score_from_rubber_results, score_from_set_points, and rubber_winner_from_sets.

Pure functions, no app context needed.
"""

from types import SimpleNamespace

from app.models import (
    LEAGUE_FORMAT_NE_WINTER,
    RubberSlot,
    build_rubber_plan,
    rubber_winner_from_sets,
    score_from_rubber_results,
    score_from_set_points,
)


def _rubber(winner):
    return SimpleNamespace(winner=winner)


# ── score_from_rubber_results (cleveland_autumn, cleveland_veterans) ────────


def test_score_from_rubber_results_home_wins_majority():
    """Score from rubber results home wins majority."""
    rubbers = [_rubber("home")] * 5 + [_rubber("away")] * 4
    home, away = score_from_rubber_results(rubbers)
    # 5*2 + 4 bonus = 14, 4*2 + 0 bonus = 8
    assert (home, away) == (14, 8)


def test_score_from_rubber_results_away_wins_majority():
    """Score from rubber results away wins majority."""
    rubbers = [_rubber("home")] * 2 + [_rubber("away")] * 4
    home, away = score_from_rubber_results(rubbers)
    assert (home, away) == (4, 12)


def test_score_from_rubber_results_tied_rubbers_split_bonus():
    """Score from rubber results tied rubbers split bonus."""
    rubbers = [_rubber("home")] * 3 + [_rubber("away")] * 3
    home, away = score_from_rubber_results(rubbers)
    # equal wins -> bonus split 2-2
    assert (home, away) == (8, 8)


def test_score_from_rubber_results_with_tied_rubber():
    """Score from rubber results with tied rubber."""
    rubbers = [_rubber("home"), _rubber("away"), _rubber("tie")]
    home, away = score_from_rubber_results(rubbers)
    # home: 1 win (2pts) + 1 tie (1pt) = 3, plus bonus since wins are equal -> split 2-2
    assert (home, away) == (5, 5)


def test_score_from_rubber_results_veterans_six_rubbers():
    """Applies unchanged to a 6-rubber Veterans fixture (4 home wins, 2 away)."""
    rubbers = [_rubber("home")] * 4 + [_rubber("away")] * 2
    home, away = score_from_rubber_results(rubbers)
    assert (home, away) == (4 * 2 + 4, 2 * 2 + 0)


# ── score_from_set_points (ne_winter) ────────────────────────────────────────


def _ne_slot(rubber_number):
    return RubberSlot(rubber_number, "Test Rubber", sets=2)


def test_score_from_set_points_straight_win():
    """Home wins both sets of one rubber: 2-0 in points."""
    rubber = SimpleNamespace(set1_walkover=None, home_set1=6, away_set1=2, home_set2=6, away_set2=3)
    home, away = score_from_set_points([(rubber, _ne_slot(1))])
    assert (home, away) == (2, 0)
    assert rubber.winner == "home"


def test_score_from_set_points_drawn_rubber():
    """A rubber split 1-1 in sets contributes 1 point to each side, no bonus."""
    rubber = SimpleNamespace(set1_walkover=None, home_set1=6, away_set1=2, home_set2=3, away_set2=6)
    home, away = score_from_set_points([(rubber, _ne_slot(1))])
    assert (home, away) == (1, 1)
    assert rubber.winner == "tie"


def test_score_from_set_points_walkover_awards_full_slot_points():
    """A walkover forfeits the whole rubber — winner gets slot.sets points.

    No need for a second set's data to exist.
    """
    rubber = SimpleNamespace(
        set1_walkover="away", home_set1=None, away_set1=None, home_set2=None, away_set2=None
    )
    home, away = score_from_set_points([(rubber, _ne_slot(1))])
    assert (home, away) == (0, 2)
    assert rubber.winner == "away"


def test_score_from_set_points_whole_fixture_max_eight():
    """Four rubbers, home wins every set: max possible fixture score is 8-0."""
    rubbers = [
        SimpleNamespace(set1_walkover=None, home_set1=6, away_set1=0, home_set2=6, away_set2=0)
        for _ in range(4)
    ]
    slots = build_rubber_plan(SimpleNamespace(format=LEAGUE_FORMAT_NE_WINTER, pairs_per_round=None))
    home, away = score_from_set_points(zip(rubbers, slots))
    assert (home, away) == (8, 0)


def test_score_from_set_points_no_bonus_for_winning_more_rubbers():
    """Winning most rubbers outright is pure set points, with no match bonus.

    3 of 4 rubbers outright (each 2-0) plus one lost outright (0-2) is pure
    set points with no bonus: 6-2, not inflated by any match bonus.
    """
    rubbers = [
        SimpleNamespace(set1_walkover=None, home_set1=6, away_set1=0, home_set2=6, away_set2=0),
        SimpleNamespace(set1_walkover=None, home_set1=6, away_set1=0, home_set2=6, away_set2=0),
        SimpleNamespace(set1_walkover=None, home_set1=6, away_set1=0, home_set2=6, away_set2=0),
        SimpleNamespace(set1_walkover=None, home_set1=0, away_set1=6, home_set2=0, away_set2=6),
    ]
    slots = build_rubber_plan(SimpleNamespace(format=LEAGUE_FORMAT_NE_WINTER, pairs_per_round=None))
    home, away = score_from_set_points(zip(rubbers, slots))
    assert (home, away) == (6, 2)


# ── rubber_winner_from_sets (cleveland_autumn) ──────────────────────────────


def test_rubber_winner_straight_sets_home():
    """Rubber winner straight sets home."""
    assert rubber_winner_from_sets(6, 2, 6, 3) == "home"


def test_rubber_winner_straight_sets_away():
    """Rubber winner straight sets away."""
    assert rubber_winner_from_sets(2, 6, 3, 6) == "away"


def test_rubber_winner_split_decided_by_champion_tiebreak():
    """Rubber winner split decided by champion tiebreak."""
    # Home takes set 1, away takes set 2, home wins the champion tiebreak 10-8.
    assert rubber_winner_from_sets(6, 4, 4, 6, 10, 8) == "home"
    assert rubber_winner_from_sets(6, 4, 4, 6, 8, 10) == "away"


def test_rubber_winner_split_no_tiebreak_entered_yet():
    """Sets shared 1-1 with no champion tiebreak recorded yet is not decided."""
    assert rubber_winner_from_sets(6, 4, 4, 6) is None
    assert rubber_winner_from_sets(6, 4, 4, 6, None, None) is None


def test_rubber_winner_single_set_only():
    """Degenerate case: only set 1 is ever given, set 2 stays None.

    E.g. cleveland_veterans' 1-set rubbers.
    """
    assert rubber_winner_from_sets(6, 3, None, None) == "home"
    assert rubber_winner_from_sets(3, 6, None, None) == "away"


def test_rubber_winner_incomplete_or_blank():
    """Rubber winner incomplete or blank."""
    assert rubber_winner_from_sets(None, None, None, None) is None
    assert rubber_winner_from_sets(6, 6, None, None) is None
    # Set 2 has been started but only one side filled in — not yet decided.
    assert rubber_winner_from_sets(6, 4, 3, None) is None
    # Sets are shared 1-1 (via a tied set 2) with no way to award it either
    # side — not yet decided.
    assert rubber_winner_from_sets(6, 4, 6, 6) is None
