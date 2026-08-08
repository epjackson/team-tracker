"""Tests for scripts/audit_eligibility.py's chronological eligibility replay.

The key property under test is that audit_league() evaluates each fixture against
only the fixtures that came *before* it (by date, then fixture id as a same-day
tie-break) — not the player's final, current-state commitment. A naive check that
ignored chronology would false-positive on legitimate early-season fixtures played
before a player's commitment was triggered.
"""

from datetime import date, timedelta

import pytest

from app.models import (
    Division,
    Fixture,
    League,
    LeagueRestriction,
    Player,
    PlayerMatchResult,
    Rubber,
    Team,
    db,
)
from scripts.audit_eligibility import _played_player_ids, audit_league

SEASON_START = date(2026, 4, 4)


@pytest.fixture
def audit_setup(app):
    """League with Division 1 teams A-D, a commitment threshold of 3, and one player."""
    league = League(name="Men's League", start_date=SEASON_START)
    db.session.add(league)
    db.session.commit()

    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()

    teams = {}
    for letter in "ABCD":
        team = Team(name=f"Club {letter}", division_id=division.id)
        db.session.add(team)
        teams[letter] = team
    db.session.commit()

    restriction = LeagueRestriction(league_id=league.id, team_commitment_threshold=3)
    db.session.add(restriction)
    db.session.commit()

    opponent_league = League(name="Opponent League", start_date=SEASON_START)
    db.session.add(opponent_league)
    db.session.commit()
    opponent_division = Division(name="Opponent Division", league_id=opponent_league.id)
    db.session.add(opponent_division)
    db.session.commit()
    opponent = Team(name="Opponents", division_id=opponent_division.id)
    db.session.add(opponent)
    db.session.commit()

    player = Player(
        first_name="Arya",
        last_name="Kamyab",
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(player)
    db.session.commit()

    return {"league": league, "teams": teams, "player": player, "opponent": opponent}


def _play(player, team, league, opponent, on_date, rubber_count=1):
    """Record one fixture on `on_date` in which `player` plays for `team` (rubber_count rubbers)."""
    fixture = Fixture(
        date=on_date, league_id=league.id, home_team_id=team.id, away_team_id=opponent.id
    )
    db.session.add(fixture)
    db.session.commit()

    for n in range(1, rubber_count + 1):
        rubber = Rubber(fixture_id=fixture.id, rubber_number=n)
        db.session.add(rubber)
        db.session.commit()
        db.session.add(
            PlayerMatchResult(player_id=player.id, rubber_id=rubber.id, side="home", won=True)
        )
    db.session.commit()
    return fixture


def test_fixture_before_commitment_is_not_flagged(audit_setup):
    """A fixture played before the commitment threshold was reached is not a violation.

    A check that ignored chronology (e.g. just comparing every fixture against the
    player's *final* committed team) would incorrectly flag the early D fixture below,
    since by the end of the season the player is committed to C. The chronological
    replay must not flag it, because at the time it was played no commitment existed yet.
    """
    league = audit_setup["league"]
    teams = audit_setup["teams"]
    player = audit_setup["player"]
    opponent = audit_setup["opponent"]

    d = SEASON_START
    _play(player, teams["A"], league, opponent, d)  # 1 for A
    _play(player, teams["D"], league, opponent, d + timedelta(days=7))  # fine: not yet committed
    _play(player, teams["C"], league, opponent, d + timedelta(days=14))  # A(1)+C(1)=2, still ok
    _play(player, teams["C"], league, opponent, d + timedelta(days=21))  # A(1)+C(2)=3 -> commit
    _play(player, teams["D"], league, opponent, d + timedelta(days=28))  # now a real violation

    restriction = LeagueRestriction.query.filter_by(league_id=league.id).first()
    violations = audit_league(league, restriction)

    assert len(violations) == 1
    violation = violations[0]
    assert violation["fixture"].date == d + timedelta(days=28)
    assert violation["played_for"].id == teams["D"].id
    assert violation["committed_to"].id == teams["C"].id


def test_moving_up_after_natural_commitment_is_not_flagged(audit_setup):
    """Playing for a higher-ranked team after committing to a lower one is always allowed."""
    league = audit_setup["league"]
    teams = audit_setup["teams"]
    player = audit_setup["player"]
    opponent = audit_setup["opponent"]

    d = SEASON_START
    _play(player, teams["D"], league, opponent, d)
    _play(player, teams["D"], league, opponent, d + timedelta(days=7))
    _play(player, teams["D"], league, opponent, d + timedelta(days=14))  # commits to D
    _play(player, teams["A"], league, opponent, d + timedelta(days=21))  # promotion: fine
    _play(player, teams["A"], league, opponent, d + timedelta(days=28))  # still fine

    restriction = LeagueRestriction.query.filter_by(league_id=league.id).first()
    violations = audit_league(league, restriction)

    assert violations == []


def test_below_threshold_across_teams_has_no_violations(audit_setup):
    """Fixtures spread across teams that never cumulatively reach the threshold are clean."""
    league = audit_setup["league"]
    teams = audit_setup["teams"]
    player = audit_setup["player"]
    opponent = audit_setup["opponent"]

    d = SEASON_START
    _play(player, teams["A"], league, opponent, d)
    _play(player, teams["B"], league, opponent, d + timedelta(days=7))
    _play(player, teams["D"], league, opponent, d + timedelta(days=14))

    restriction = LeagueRestriction.query.filter_by(league_id=league.id).first()
    violations = audit_league(league, restriction)

    assert violations == []


def test_same_day_fixtures_broken_by_fixture_id(audit_setup):
    """Same-date fixtures are ordered by fixture id as a deterministic tie-break."""
    league = audit_setup["league"]
    teams = audit_setup["teams"]
    player = audit_setup["player"]
    opponent = audit_setup["opponent"]

    d = SEASON_START
    _play(player, teams["C"], league, opponent, d)
    _play(player, teams["C"], league, opponent, d)
    _play(player, teams["C"], league, opponent, d)  # commits to C (all same date, ascending ids)
    violating_fixture = _play(player, teams["D"], league, opponent, d)  # later id, same date

    restriction = LeagueRestriction.query.filter_by(league_id=league.id).first()
    violations = audit_league(league, restriction)

    assert len(violations) == 1
    assert violations[0]["fixture"].id == violating_fixture.id


def test_played_player_ids_deduplicates_across_rubbers(audit_setup):
    """A player appearing in multiple rubbers of the same fixture counts once."""
    league = audit_setup["league"]
    teams = audit_setup["teams"]
    player = audit_setup["player"]
    opponent = audit_setup["opponent"]

    fixture = _play(player, teams["A"], league, opponent, SEASON_START, rubber_count=3)

    home_ids = _played_player_ids(fixture.id, "home")
    away_ids = _played_player_ids(fixture.id, "away")

    assert home_ids == [player.id]
    assert away_ids == []
