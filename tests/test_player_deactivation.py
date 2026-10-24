"""Tests for deactivating a player instead of deleting them.

`deactivate_player` (POST /players/<id>/deactivate) replaces the old hard-delete
route: it only flips Player.membership_status to "inactive", leaving the row and
every reference to it (history, roster membership) untouched. From that point the
player must stop being newly selectable in squad pools, rubber pairs dropdowns,
team roster "Add Player", and captain assignment — see CLAUDE.md "Deactivating a
player".
"""

from datetime import date

from app.models import (
    Division,
    Fixture,
    FixtureSquadEntry,
    League,
    Player,
    PlayerMatchResult,
    Rubber,
    Team,
    db,
)

SEASON_START = date(2026, 4, 4)


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_uid"] = "test-admin"
        sess["user_role"] = "admin"
    return client


def _make_league_team(name="Team A"):
    league = League(name="Men's League", start_date=SEASON_START)
    db.session.add(league)
    db.session.commit()
    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()
    team = Team(name=name, division_id=division.id)
    db.session.add(team)
    db.session.commit()
    return league, team


def _make_player(first="Test", last="Player", gender="M"):
    player = Player(
        first_name=first,
        last_name=last,
        gender=gender,
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(player)
    db.session.commit()
    return player


def test_deactivate_keeps_row_and_history_intact(app):
    """Deactivating flips membership_status and leaves everything else untouched."""
    league, team = _make_league_team()
    player = _make_player()
    team.players.append(player)
    db.session.commit()

    fixture = Fixture(
        date=SEASON_START, league_id=league.id, home_team_id=team.id, away_team_id=team.id
    )
    db.session.add(fixture)
    db.session.commit()
    rubber = Rubber(fixture_id=fixture.id, rubber_number=1, winner="home")
    db.session.add(rubber)
    db.session.commit()
    db.session.add(
        PlayerMatchResult(player_id=player.id, rubber_id=rubber.id, side="home", won=True)
    )
    db.session.commit()

    player_id, team_id = player.id, team.id
    client = _admin_client(app)
    resp = client.post(f"/players/{player_id}/deactivate")
    assert resp.status_code == 302

    reloaded = Player.query.get(player_id)
    assert reloaded is not None
    assert reloaded.membership_status == "inactive"
    assert team_id in {t.id for t in reloaded.teams}
    assert PlayerMatchResult.query.filter_by(player_id=player_id).count() == 1


def test_inactive_player_excluded_from_add_player_dropdown_and_rejected_server_side(app):
    """An inactive player can't be newly rostered, via the dropdown or a direct POST."""
    league, team = _make_league_team()
    other_team = Team(name="Team B", division_id=team.division_id)
    db.session.add(other_team)
    db.session.commit()
    player = _make_player()
    player.membership_status = "inactive"
    db.session.commit()

    client = _admin_client(app)
    resp = client.get(f"/teams/{team.id}")
    assert player.name.encode() not in _extract_add_player_select(resp.data)

    resp = client.post("/players", data={"team_id": other_team.id, "player_id": [str(player.id)]})
    assert resp.status_code == 302
    assert player.id not in {t.id for t in other_team.players}


def _extract_add_player_select(html):
    start = html.find(b'id="player_id"')
    end = html.find(b"</select>", start)
    return html[start:end]


def test_squad_pool_marks_inactive_player_not_selectable_but_keeps_existing_entry(app):
    """_build_squad_pool blocks a fresh pick but keeps an already-reserved inactive player."""
    league, team = _make_league_team()
    away = Team(name="Away", division_id=team.division_id)
    db.session.add(away)
    db.session.commit()

    fresh = _make_player("Fresh", "Inactive")
    fresh.membership_status = "inactive"
    reserved = _make_player("Already", "Reserved")
    reserved.membership_status = "inactive"
    team.players.append(fresh)
    team.players.append(reserved)
    db.session.commit()

    fixture = Fixture(
        date=SEASON_START, league_id=league.id, home_team_id=team.id, away_team_id=away.id
    )
    db.session.add(fixture)
    db.session.commit()
    db.session.add(FixtureSquadEntry(fixture_id=fixture.id, player_id=reserved.id, team_id=team.id))
    db.session.commit()

    client = _admin_client(app)
    resp = client.get(f"/fixtures/{fixture.id}?team_id={team.id}")
    assert resp.status_code == 200
    body = resp.data
    # The already-reserved inactive player's checkbox is present and checked.
    assert f'id="squad_{reserved.id}"'.encode() in body
    reserved_row = body[body.index(f'id="squad_{reserved.id}"'.encode()) :]
    assert b"checked" in reserved_row[: reserved_row.index(b">")]
    # The never-reserved inactive player is marked ineligible via the reason tooltip.
    assert b"Player is inactive" in body


def test_set_team_captain_rejects_inactive_player(app):
    """An inactive player can't be newly assigned as captain."""
    league, team = _make_league_team()
    player = _make_player()
    player.membership_status = "inactive"
    team.players.append(player)
    db.session.commit()

    client = _admin_client(app)
    resp = client.post(f"/teams/{team.id}/captain", data={"player_id": player.id})
    assert resp.status_code == 302
    from app.models import TeamCaptain

    assert TeamCaptain.query.filter_by(team_id=team.id, player_id=player.id).count() == 0
