"""Tests for save_fixture_squad (POST /fixtures/<id>/squad).

Regression: a single-Yarm fixture's squad page lists every FixtureSquadEntry for the
fixture regardless of team_id, but saving with a team_id only deleted that team's
rows — so a reserved player whose row had team_id NULL (or another team's id) was
re-inserted and hit the uq_fixture_player_squad unique constraint (500 error).
"""

from datetime import date

from app.models import Division, Fixture, FixtureSquadEntry, League, Player, Team, db

SEASON_START = date(2026, 4, 4)


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_uid"] = "test-admin"
        sess["user_role"] = "admin"
    return client


def _make_teams(*names):
    league = League(name="Men's League", start_date=SEASON_START)
    db.session.add(league)
    db.session.commit()
    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()
    teams = [Team(name=n, division_id=division.id) for n in names]
    db.session.add_all(teams)
    db.session.commit()
    return league, teams


def _make_player(first, last="Player"):
    player = Player(
        first_name=first,
        last_name=last,
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(player)
    db.session.commit()
    return player


def test_single_yarm_deselect_with_untagged_existing_rows(app):
    """Deselecting one player saves cleanly when existing rows have no team_id."""
    league, (team,) = _make_teams("Yarm A")
    fixture = Fixture(
        date=SEASON_START, league_id=league.id, home_team_id=team.id, away_team_name="Opp"
    )
    db.session.add(fixture)
    db.session.commit()
    keep, drop = _make_player("Keep"), _make_player("Drop")
    db.session.add_all(
        [
            FixtureSquadEntry(fixture_id=fixture.id, player_id=keep.id, team_id=None),
            FixtureSquadEntry(fixture_id=fixture.id, player_id=drop.id, team_id=None),
        ]
    )
    db.session.commit()
    fixture_id, team_id, keep_id = fixture.id, team.id, keep.id

    resp = _admin_client(app).post(
        f"/fixtures/{fixture_id}/squad",
        data={"team_id": str(team_id), "squad_player_ids": [str(keep_id)]},
    )
    assert resp.status_code == 302
    rows = FixtureSquadEntry.query.filter_by(fixture_id=fixture_id).all()
    assert [(r.player_id, r.team_id) for r in rows] == [(keep_id, team_id)]


def test_dual_yarm_save_preserves_other_team_and_skips_its_players(app):
    """Dual-Yarm: other team's rows survive; ticking one of its players doesn't 500."""
    league, (home, away) = _make_teams("Yarm A", "Yarm B")
    fixture = Fixture(
        date=SEASON_START, league_id=league.id, home_team_id=home.id, away_team_id=away.id
    )
    db.session.add(fixture)
    db.session.commit()
    mine, theirs = _make_player("Mine"), _make_player("Theirs")
    db.session.add_all(
        [
            FixtureSquadEntry(fixture_id=fixture.id, player_id=mine.id, team_id=home.id),
            FixtureSquadEntry(fixture_id=fixture.id, player_id=theirs.id, team_id=away.id),
        ]
    )
    db.session.commit()
    fixture_id, home_id, away_id = fixture.id, home.id, away.id
    mine_id, theirs_id = mine.id, theirs.id

    resp = _admin_client(app).post(
        f"/fixtures/{fixture_id}/squad",
        data={"team_id": str(home_id), "squad_player_ids": [str(mine_id), str(theirs_id)]},
    )
    assert resp.status_code == 302
    rows = {
        (r.player_id, r.team_id)
        for r in FixtureSquadEntry.query.filter_by(fixture_id=fixture_id).all()
    }
    assert rows == {(mine_id, home_id), (theirs_id, away_id)}
