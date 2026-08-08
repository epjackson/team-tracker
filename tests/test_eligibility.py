"""Tests for check_player_eligibility()'s team-commitment threshold logic.

The threshold counts fixtures cumulatively across a team and every team ranked
above it (better division, or earlier letter within a division) — not per
specific team. See CLAUDE.md's "Eligibility system" section.
"""

import pytest

from app.models import (
    Division,
    Fixture,
    League,
    LeagueRestriction,
    Player,
    PlayerMatchResult,
    PlayerTeamCommitment,
    Rubber,
    Team,
    check_player_eligibility,
    db,
)

SEASON_START = __import__("datetime").date(2026, 4, 4)


@pytest.fixture
def league_setup(app):
    """Create a league with Division 1 containing teams A-D and a commitment threshold of 3."""
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

    # Opponent team lives in an unrelated league/division so it never counts
    # towards the cumulative threshold scan for the league under test.
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


def _play_fixtures(player, team, league, count, opponent):
    """Record `count` separate fixtures in which `player` played one rubber for `team`."""
    for _ in range(count):
        fixture = Fixture(
            date=SEASON_START,
            league_id=league.id,
            home_team_id=team.id,
            away_team_id=opponent.id,
        )
        db.session.add(fixture)
        db.session.commit()

        rubber = Rubber(fixture_id=fixture.id, rubber_number=1)
        db.session.add(rubber)
        db.session.commit()

        db.session.add(
            PlayerMatchResult(player_id=player.id, rubber_id=rubber.id, side="home", won=True)
        )
        db.session.commit()


def test_cumulative_fixtures_across_teams_trigger_commitment(league_setup):
    """1 fixture for A + 2 for C reaches a threshold of 3, tying the player to C."""
    league = league_setup["league"]
    teams = league_setup["teams"]
    player = league_setup["player"]
    opponent = league_setup["opponent"]

    _play_fixtures(player, teams["A"], league, 1, opponent)
    _play_fixtures(player, teams["C"], league, 2, opponent)
    _play_fixtures(player, teams["D"], league, 2, opponent)

    assert check_player_eligibility(player.id, teams["A"].id, league.id)["eligible"] is True
    assert check_player_eligibility(player.id, teams["B"].id, league.id)["eligible"] is True
    assert check_player_eligibility(player.id, teams["C"].id, league.id)["eligible"] is True
    assert check_player_eligibility(player.id, teams["D"].id, league.id)["eligible"] is False

    elig_c = check_player_eligibility(player.id, teams["C"].id, league.id)
    assert elig_c["effective_committed_team_id"] == teams["C"].id
    assert elig_c["commitment_info"] == "Club C"


def test_below_threshold_is_fully_eligible(league_setup):
    """1 fixture for A + 1 for C is only 2 cumulative fixtures — below the threshold of 3."""
    league = league_setup["league"]
    teams = league_setup["teams"]
    player = league_setup["player"]
    opponent = league_setup["opponent"]

    _play_fixtures(player, teams["A"], league, 1, opponent)
    _play_fixtures(player, teams["C"], league, 1, opponent)

    for letter in "ABCD":
        elig = check_player_eligibility(player.id, teams[letter].id, league.id)
        assert elig["eligible"] is True
        assert elig["effective_committed_team_id"] is None


def test_single_team_threshold_still_locks(league_setup):
    """3 fixtures for a single team ties the player to it, same as before this fix."""
    league = league_setup["league"]
    teams = league_setup["teams"]
    player = league_setup["player"]
    opponent = league_setup["opponent"]

    _play_fixtures(player, teams["B"], league, 3, opponent)

    assert check_player_eligibility(player.id, teams["A"].id, league.id)["eligible"] is True
    assert check_player_eligibility(player.id, teams["B"].id, league.id)["eligible"] is True
    assert check_player_eligibility(player.id, teams["C"].id, league.id)["eligible"] is False
    assert check_player_eligibility(player.id, teams["D"].id, league.id)["eligible"] is False


def test_formal_commitment_blocks_same_or_lower_team(league_setup):
    """A formal PlayerTeamCommitment record blocks same/lower teams but allows moving up."""
    league = league_setup["league"]
    teams = league_setup["teams"]
    player = league_setup["player"]

    db.session.add(
        PlayerTeamCommitment(player_id=player.id, team_id=teams["C"].id, league_id=league.id)
    )
    db.session.commit()

    assert check_player_eligibility(player.id, teams["A"].id, league.id)["eligible"] is True
    assert check_player_eligibility(player.id, teams["C"].id, league.id)["eligible"] is True
    assert check_player_eligibility(player.id, teams["D"].id, league.id)["eligible"] is False


def test_no_restriction_configured_means_always_eligible(app):
    """With no LeagueRestriction row, cumulative fixtures never trigger a commitment."""
    league = League(name="Friendly League", start_date=SEASON_START)
    db.session.add(league)
    db.session.commit()

    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()

    team = Team(name="Club A", division_id=division.id)
    opponent = Team(name="Opponents", division_id=division.id)
    db.session.add_all([team, opponent])
    db.session.commit()

    player = Player(
        first_name="Free",
        last_name="Agent",
        gender="F",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(player)
    db.session.commit()

    _play_fixtures(player, team, league, 10, opponent)

    elig = check_player_eligibility(player.id, team.id, league.id)
    assert elig["eligible"] is True
    assert elig["effective_committed_team_id"] is None
