"""New players may only be registered by an admin.

Per CLAUDE.md's "Admin-only routes": manual_fixture_entry is also reachable
by a captain as the pitchside confirm step, so a captain confirming a
scoresheet with a name that doesn't match any existing Player must be
blocked rather than silently creating one via _get_or_create_player's
fallback.
"""

from datetime import date

from app.models import Division, Fixture, League, Player, Team, TeamCaptain, db

SEASON_START = date(2026, 4, 4)


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_uid"] = "test-admin"
        sess["user_role"] = "admin"
    return client


def _captain_client(app, captain_player_id):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_uid"] = "test-captain"
        sess["user_role"] = "captain"
        sess["captain_player_id"] = captain_player_id
    return client


def _make_fixture(home_name="Home", away_name="Away"):
    league = League(name="Autumn League", start_date=SEASON_START)
    db.session.add(league)
    db.session.commit()
    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()
    home = Team(name=home_name, division_id=division.id)
    away = Team(name=away_name, division_id=division.id)
    db.session.add_all([home, away])
    db.session.commit()
    fixture = Fixture(
        date=SEASON_START, league_id=league.id, home_team_id=home.id, away_team_id=away.id
    )
    db.session.add(fixture)
    db.session.commit()
    return league, home, away, fixture


def _make_captain(team):
    player = Player(
        first_name="Cap",
        last_name="Tain",
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(player)
    db.session.commit()
    team.players.append(player)
    db.session.add(TeamCaptain(player_id=player.id, team_id=team.id, season="2026"))
    db.session.commit()
    return player


def _post_confirm(client, fixture, home_players, away_players):
    return client.post(
        "/fixtures/manual",
        data={
            "fixture_id": str(fixture.id),
            "rubber_count": "1",
            "rubber_0_home_set1": "6",
            "rubber_0_away_set1": "2",
            "rubber_0_home_set2": "6",
            "rubber_0_away_set2": "3",
            "rubber_0_home_players": home_players,
            "rubber_0_away_players": away_players,
        },
    )


def test_captain_confirm_blocked_for_unregistered_player(app):
    """Captain confirm blocked for unregistered player."""
    league, home, away, fixture = _make_fixture()
    captain = _make_captain(home)
    away_player = Player(
        first_name="Away",
        last_name="Player",
        gender="F",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(away_player)
    db.session.commit()

    before_count = Player.query.count()

    client = _captain_client(app, captain.id)
    resp = _post_confirm(client, fixture, "Cap Tain", "Away Player, New Stranger")
    assert resp.status_code == 302

    # No new player was created, and the fixture's rubbers were not saved.
    assert Player.query.count() == before_count
    assert fixture.rubbers.count() == 0


def test_captain_confirm_succeeds_with_only_existing_players(app):
    """Captain confirm succeeds with only existing players."""
    league, home, away, fixture = _make_fixture()
    captain = _make_captain(home)
    away_player = Player(
        first_name="Away",
        last_name="Player",
        gender="F",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(away_player)
    db.session.commit()

    before_count = Player.query.count()

    client = _captain_client(app, captain.id)
    resp = _post_confirm(client, fixture, "Cap Tain", "Away Player")
    assert resp.status_code == 302

    assert Player.query.count() == before_count
    assert fixture.rubbers.count() == 1


def test_admin_confirm_still_registers_new_player(app):
    """Admin confirm still registers new player."""
    league, home, away, fixture = _make_fixture()
    before_count = Player.query.count()

    client = _admin_client(app)
    resp = _post_confirm(client, fixture, "Brand New", "Also New")
    assert resp.status_code == 302

    assert Player.query.count() == before_count + 2
    assert fixture.rubbers.count() == 1
