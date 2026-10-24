"""Tests for the league-season scoping helpers and the /select-season route."""

import re
from datetime import date

from app.models import (
    Division,
    Fixture,
    League,
    ParentLeague,
    Player,
    Team,
    db,
    fixtures_query_for_league,
    players_query_for_league,
    teams_query_for_league,
)

SEASON_START = date(2026, 4, 4)


def _make_league(name):
    league = League(name=name, start_date=SEASON_START)
    db.session.add(league)
    db.session.commit()
    return league


def _make_season_league(name, season_type, start_date=SEASON_START):
    """Create a League under a fresh ParentLeague tagged with season_type."""
    parent = ParentLeague(
        name=f"{name} Parent",
        season_type=season_type,
        pairs_per_round=3,
    )
    db.session.add(parent)
    db.session.flush()
    league = League(name=name, start_date=start_date, parent_league_id=parent.id)
    db.session.add(league)
    db.session.commit()
    return league


def _make_division(league, name="Division 1"):
    division = Division(name=name, league_id=league.id)
    db.session.add(division)
    db.session.commit()
    return division


def _make_team(name, division=None):
    team = Team(name=name, division_id=division.id if division else None)
    db.session.add(team)
    db.session.commit()
    return team


def test_teams_query_for_league_scopes_by_division(app):
    """Only teams whose division belongs to the given league are returned."""
    league_a = _make_league("League A")
    league_b = _make_league("League B")
    div_a = _make_division(league_a)
    div_b = _make_division(league_b)
    team_a = _make_team("Team A", div_a)
    team_b = _make_team("Team B", div_b)
    unassigned = _make_team("Unassigned Team")

    scoped = teams_query_for_league(league_a.id).all()
    assert scoped == [team_a]

    all_teams = teams_query_for_league(None).all()
    assert {t.id for t in all_teams} == {team_a.id, team_b.id, unassigned.id}


def test_fixtures_query_for_league_uses_division_fallback(app):
    """A fixture with no direct league_id is still matched via its teams' division."""
    league = _make_league("League A")
    division = _make_division(league)
    home = _make_team("Home", division)
    away = _make_team("Away", division)

    other_league = _make_league("League B")

    direct_fixture = Fixture(
        date=SEASON_START, league_id=league.id, home_team_id=home.id, away_team_id=away.id
    )
    fallback_fixture = Fixture(
        date=SEASON_START, league_id=None, home_team_id=home.id, away_team_id=away.id
    )
    db.session.add_all([direct_fixture, fallback_fixture])
    db.session.commit()

    scoped_ids = {f.id for f in fixtures_query_for_league(league.id).all()}
    assert scoped_ids == {direct_fixture.id, fallback_fixture.id}

    other_scoped = fixtures_query_for_league(other_league.id).all()
    assert other_scoped == []

    assert len(fixtures_query_for_league(None).all()) == 2


def test_players_query_for_league_scopes_by_roster(app):
    """Only players rostered on a team within the league's divisions are returned."""
    league = _make_league("League A")
    division = _make_division(league)
    team = _make_team("Team A", division)
    other_team = _make_team("Team B")  # unassigned, different league scope

    in_league = Player(
        first_name="In",
        last_name="League",
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    out_of_league = Player(
        first_name="Out",
        last_name="OfLeague",
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add_all([in_league, out_of_league])
    db.session.commit()

    team.players.append(in_league)
    other_team.players.append(out_of_league)
    db.session.commit()

    scoped = players_query_for_league(league.id).all()
    assert scoped == [in_league]

    all_players = players_query_for_league(None).all()
    assert {p.id for p in all_players} == {in_league.id, out_of_league.id}


def test_select_season_sets_and_clears_session(app):
    """Selecting a season sets the session key; selecting none clears it."""
    _make_season_league("Autumn League 2026", "autumn")
    client = app.test_client()

    resp = client.get("/select-season?season_type=autumn")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess["selected_season_type"] == "autumn"

    resp = client.get("/select-season")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "selected_season_type" not in sess


def test_select_season_ignores_invalid_type(app):
    """A season_type that isn't one of the four badges is treated as clearing the selection."""
    client = app.test_client()
    resp = client.get("/select-season?season_type=not-a-real-season")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "selected_season_type" not in sess


def test_team_detail_fixtures_scoped_to_selected_season(app):
    """A team moved to a new league only shows that league's fixtures once its season is selected.

    Regression test: a team reallocated to a different division (e.g. Autumn League
    Division 1) previously kept showing every fixture it had ever played, including
    fixtures from a league it's no longer in.
    """
    old_league = _make_season_league("Summer League 2026", "summer")
    new_league = _make_season_league("Autumn League 2026", "autumn")
    old_division = _make_division(old_league)
    new_division = _make_division(new_league)
    home = _make_team("Home", old_division)
    away = _make_team("Away", old_division)

    old_fixture = Fixture(
        date=SEASON_START,
        league_id=old_league.id,
        home_team_id=home.id,
        away_team_id=away.id,
    )
    db.session.add(old_fixture)
    db.session.commit()

    # Reallocate the team to the new league's division, as done via league_detail.
    home.division_id = new_division.id
    db.session.commit()

    client = app.test_client()

    # No season selected: every fixture the team has ever played is shown.
    resp = client.get(f"/teams/{home.id}")
    assert old_fixture_seen(resp) is True

    # Select the new season: the old league's fixture should no longer appear.
    client.get("/select-season?season_type=autumn")
    resp = client.get(f"/teams/{home.id}")
    assert old_fixture_seen(resp) is False

    # Select the old season: the fixture is visible again.
    client.get("/select-season?season_type=summer")
    resp = client.get(f"/teams/{home.id}")
    assert old_fixture_seen(resp) is True


def old_fixture_seen(resp):
    """Whether the fixtures card in a team_detail response lists any fixture."""
    return b"No fixtures recorded yet." not in resp.data


def test_player_summary_list_is_never_season_scoped(app):
    """The player list on /players/summary always shows every club player.

    Regression test: name/gender/membership/team-play-interest are portable, club-wide
    attributes, not season data, so selecting a season badge must not hide a player who
    isn't rostered on a team in that season.
    """
    league = _make_season_league("Autumn League 2026", "autumn")
    division = _make_division(league)
    team = _make_team("Team A", division)

    in_league = Player(
        first_name="In",
        last_name="League",
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    out_of_league = Player(
        first_name="Out",
        last_name="OfLeague",
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add_all([in_league, out_of_league])
    db.session.commit()
    team.players.append(in_league)
    db.session.commit()

    client = app.test_client()
    client.get("/select-season?season_type=autumn")
    resp = client.get("/players/summary")
    assert b"In League" in resp.data
    assert b"Out OfLeague" in resp.data


def test_player_summary_teams_and_fixtures_columns_scoped_to_season(app):
    """Teams/Fixtures column values change with the season badge even though the row stays."""
    autumn = _make_season_league("Autumn League 2026", "autumn")
    summer = _make_season_league("Summer League 2026", "summer")
    autumn_division = _make_division(autumn, "Autumn Div 1")
    summer_division = _make_division(summer, "Summer Div 1")
    autumn_team = _make_team("Autumn Team", autumn_division)
    summer_team = _make_team("Summer Team", summer_division)

    player = Player(
        first_name="Multi",
        last_name="Season",
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(player)
    db.session.commit()
    autumn_team.players.append(player)
    summer_team.players.append(player)
    db.session.commit()

    client = app.test_client()

    def total_teams_badge(resp):
        """Extract the "Teams" column badge count for the row after the player's name."""
        row = resp.data[resp.data.index(b"Multi Season") :]
        match = re.search(rb'<span class="badge bg-info">(\d+)</span>', row)
        return int(match.group(1))

    resp = client.get("/players/summary")
    assert total_teams_badge(resp) == 2

    client.get("/select-season?season_type=autumn")
    resp = client.get("/players/summary")
    assert total_teams_badge(resp) == 1
