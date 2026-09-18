"""Unit tests for build_rubber_plan and friends — no app context needed, pure functions."""

from types import SimpleNamespace

from app.models import (
    LEAGUE_FORMAT_CLEVELAND_AUTUMN,
    LEAGUE_FORMAT_CLEVELAND_VETERANS,
    LEAGUE_FORMAT_NE_WINTER,
    build_rubber_plan,
    league_format_description,
    rubber_count_for_league,
    walkover_max_score,
)


def _league(format=LEAGUE_FORMAT_CLEVELAND_AUTUMN, pairs_per_round=None, parent_league=None):
    return SimpleNamespace(
        format=format, pairs_per_round=pairs_per_round, parent_league=parent_league
    )


def _parent(sets_per_rubber=None, games_per_set=None):
    return SimpleNamespace(sets_per_rubber=sets_per_rubber, games_per_set=games_per_set)


def test_autumn_is_a_full_round_robin_for_various_pairs_per_round():
    """Cleveland Autumn is a genuine round-robin for any pairs_per_round.

    Every home pair plays every away pair exactly once, so there are always
    n_pairs rounds (not a fixed 3, which only coincided with reality when
    pairs_per_round was 3) — giving n_pairs^2 total rubbers, numbered
    contiguously at a stride of n_pairs.
    """
    for n_pairs in (2, 3, 4):
        plan = build_rubber_plan(_league(pairs_per_round=n_pairs))
        assert len(plan) == n_pairs * n_pairs
        assert [slot.rubber_number for slot in plan] == list(range(1, n_pairs * n_pairs + 1))
        assert all(slot.sets == 2 for slot in plan)
        assert all(slot.home_slots is None and slot.away_slots is None for slot in plan)


def test_autumn_sets_per_rubber_cascades_from_parent_league():
    """sets_per_rubber cascades from the parent league.

    It decides Summer's single set (no decider) vs Autumn's 2 sets plus a
    champion's tiebreak — despite both sharing the cleveland_autumn format.
    """
    summer_plan = build_rubber_plan(
        _league(pairs_per_round=3, parent_league=_parent(sets_per_rubber=1, games_per_set=8))
    )
    assert len(summer_plan) == 9
    assert all(slot.sets == 1 for slot in summer_plan)

    autumn_plan = build_rubber_plan(
        _league(pairs_per_round=2, parent_league=_parent(sets_per_rubber=2, games_per_set=6))
    )
    assert len(autumn_plan) == 4
    assert all(slot.sets == 2 for slot in autumn_plan)


def test_autumn_no_league_defaults_to_three_pairs():
    """No league (or no pairs_per_round set) falls back to 3 pairs, 2 sets."""
    assert len(build_rubber_plan(None)) == 9
    assert len(build_rubber_plan(_league(pairs_per_round=None))) == 9


def test_veterans_plan_shape():
    """6 rubbers: Mens Doubles, Womens Doubles, then a full 2x2 mixed round robin."""
    plan = build_rubber_plan(_league(format=LEAGUE_FORMAT_CLEVELAND_VETERANS))
    assert len(plan) == 6
    assert [slot.label for slot in plan] == [
        "Mens Doubles",
        "Womens Doubles",
        "Mixed 1 v Mixed 1",
        "Mixed 1 v Mixed 2",
        "Mixed 2 v Mixed 1",
        "Mixed 2 v Mixed 2",
    ]
    assert all(slot.sets == 1 for slot in plan)


def test_veterans_mens_womens_doubles_symmetric_slots():
    """Mens/Womens Doubles use the same 2 players regardless of perspective."""
    plan = build_rubber_plan(_league(format=LEAGUE_FORMAT_CLEVELAND_VETERANS))
    mens, womens = plan[0], plan[1]
    assert mens.home_slots == mens.away_slots == ("pair1_man", "pair2_man")
    assert womens.home_slots == womens.away_slots == ("pair1_woman", "pair2_woman")


def test_veterans_same_pair_mixed_rubbers_symmetric():
    """Mixed 1 v Mixed 1 and Mixed 2 v Mixed 2 are symmetric between perspectives."""
    plan = build_rubber_plan(_league(format=LEAGUE_FORMAT_CLEVELAND_VETERANS))
    r3, r6 = plan[2], plan[5]
    assert r3.home_slots == r3.away_slots == ("pair1_man", "pair1_woman")
    assert r6.home_slots == r6.away_slots == ("pair2_man", "pair2_woman")


def test_veterans_cross_mixed_rubbers_asymmetric_slots():
    """The two 'cross' rubbers must use different slots for home vs away.

    pair1-v-pair2 and pair2-v-pair1 — each side fields a different one of
    their own two mixed pairs. This is the bug the initial draft plan got
    wrong.
    """
    plan = build_rubber_plan(_league(format=LEAGUE_FORMAT_CLEVELAND_VETERANS))
    r4, r5 = plan[3], plan[4]
    assert r4.label == "Mixed 1 v Mixed 2"
    assert r4.home_slots == ("pair1_man", "pair1_woman")
    assert r4.away_slots == ("pair2_man", "pair2_woman")
    assert r4.home_slots != r4.away_slots

    assert r5.label == "Mixed 2 v Mixed 1"
    assert r5.home_slots == ("pair2_man", "pair2_woman")
    assert r5.away_slots == ("pair1_man", "pair1_woman")
    assert r5.home_slots != r5.away_slots


def test_ne_winter_plan_shape():
    """4 fixed rubbers, no rotation, 2 sets each."""
    plan = build_rubber_plan(_league(format=LEAGUE_FORMAT_NE_WINTER))
    assert len(plan) == 4
    assert [slot.label for slot in plan] == [
        "Mens Doubles",
        "Womens Doubles",
        "Mixed Pair 1",
        "Mixed Pair 2",
    ]
    assert all(slot.sets == 2 for slot in plan)
    # Every NE Winter rubber is symmetric — no cross-pairing rubbers exist.
    assert all(slot.home_slots == slot.away_slots for slot in plan)


def test_rubber_count_for_league():
    """Rubber count for league."""
    assert rubber_count_for_league(_league(pairs_per_round=3)) == 9
    assert rubber_count_for_league(_league(format=LEAGUE_FORMAT_CLEVELAND_VETERANS)) == 6
    assert rubber_count_for_league(_league(format=LEAGUE_FORMAT_NE_WINTER)) == 4


def test_walkover_max_score_autumn():
    """Existing formula: n_pairs^2 rubbers * 2 pts + 4 bonus."""
    assert walkover_max_score(_league(pairs_per_round=3)) == 3 * 3 * 2 + 4
    assert walkover_max_score(_league(pairs_per_round=2)) == 2 * 2 * 2 + 4


def test_walkover_max_score_veterans():
    """6 rubbers * 2 pts + 4 bonus = 16."""
    assert walkover_max_score(_league(format=LEAGUE_FORMAT_CLEVELAND_VETERANS)) == 16


def test_walkover_max_score_ne_winter():
    """Pure set points, no bonus: 4 rubbers * 2 sets = 8."""
    assert walkover_max_score(_league(format=LEAGUE_FORMAT_NE_WINTER)) == 8


def _cleveland_autumn_parent(pairs_per_round, sets_per_rubber, games_per_set, tiebreak_at_games):
    return SimpleNamespace(
        format=LEAGUE_FORMAT_CLEVELAND_AUTUMN,
        pairs_per_round=pairs_per_round,
        sets_per_rubber=sets_per_rubber,
        games_per_set=games_per_set,
        tiebreak_at_games=tiebreak_at_games,
    )


def test_league_format_description_single_set_has_no_second_set_or_decider():
    """Summer must describe a single set with no champion's tiebreak.

    sets_per_rubber=1 — the description previously always claimed "2 sets
    ... champion's tiebreak" regardless of the parent's real sets_per_rubber.
    """
    summer = _cleveland_autumn_parent(
        pairs_per_round=3, sets_per_rubber=1, games_per_set=8, tiebreak_at_games=8
    )
    desc = league_format_description(summer)
    assert "3 pairs" in desc
    assert "9 rubbers" in desc
    assert "single set to 8 games" in desc
    assert "champion's" not in desc
    assert "2 sets" not in desc


def test_league_format_description_two_sets_describes_champion_tiebreak():
    """Autumn (sets_per_rubber=2) must describe 2 sets plus the champion's tiebreak decider."""
    autumn = _cleveland_autumn_parent(
        pairs_per_round=2, sets_per_rubber=2, games_per_set=6, tiebreak_at_games=6
    )
    desc = league_format_description(autumn)
    assert "2 pairs" in desc
    assert "4 rubbers" in desc
    assert "2 sets to 6 games" in desc
    assert "champion's" in desc
