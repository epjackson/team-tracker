"""The pitchside Match Day entry screen must follow the fixture's real format.

Its actual league format (rubber count/shape), including for fixtures that
don't carry their own league_id and only resolve their league via the team's
division — the same fallback save_rubbers/fixture_detail use via
_effective_league().
"""

from datetime import date

from app.models import (
    LEAGUE_FORMAT_NE_WINTER,
    Division,
    Fixture,
    League,
    Player,
    Team,
    TeamCaptain,
    db,
)


def _make_ne_winter_fixture():
    league = League(
        name="NE Winter League 2026",
        start_date=date(2026, 1, 1),
        format=LEAGUE_FORMAT_NE_WINTER,
    )
    db.session.add(league)
    db.session.commit()
    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()
    home = Team(name="Home", division_id=division.id)
    away = Team(name="Away", division_id=division.id)
    db.session.add_all([home, away])
    db.session.commit()
    captain = Player(
        first_name="Cap",
        last_name="Tain",
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(captain)
    db.session.commit()
    home.players.append(captain)
    db.session.add(TeamCaptain(player_id=captain.id, team_id=home.id, season="2026"))
    db.session.commit()
    # No league_id set — mirrors fixtures created before/without explicit tagging;
    # the format must still be resolved via home_team.division.league.
    fixture = Fixture(date=date(2026, 9, 1), home_team_id=home.id, away_team_id=away.id)
    db.session.add(fixture)
    db.session.commit()
    return fixture, captain


def _pitchside_client(app, player_id, fixture_id):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["pitchside_player_id"] = player_id
        sess["pitchside_fixture_id"] = fixture_id
    return client


def test_entry_uses_effective_league_format_when_fixture_league_unset(app):
    """Entry uses effective league format when fixture league unset."""
    fixture, captain = _make_ne_winter_fixture()
    assert fixture.league_id is None  # sanity: this is the fallback scenario

    client = _pitchside_client(app, captain.id, fixture.id)
    resp = client.get("/pitchside/entry")
    assert resp.status_code == 200
    body = resp.data.decode()

    # NE Winter's fixed-squad shape (4 rubbers) rendered, not Cleveland Autumn's
    # default 3-pairs/9-rubbers layout.
    assert 'const LEAGUE_FORMAT = "ne_winter";' in body
    assert '"rubber_number": 4' in body
    assert '"rubber_number": 9' not in body
