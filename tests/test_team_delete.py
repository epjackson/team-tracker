"""Regression test for deleting a team with dependent rows.

Several dependent tables (player_team_commitments, team_captains,
fixture_squad_entries, match_fees_paid, match_day_drafts) have NOT NULL foreign
keys to team_id/fixture_id with no ORM-level cascade configured, so deleting a
team via plain db.session.delete(team) used to raise an IntegrityError as soon
as any such row existed. See CLAUDE.md / delete_team in app/__init__.py.
"""

from datetime import date

from app.models import (
    Division,
    Fixture,
    FixtureSquadEntry,
    League,
    MatchDayDraft,
    MatchFeePaid,
    Player,
    PlayerMatchResult,
    PlayerTeamCommitment,
    Rubber,
    Team,
    TeamCaptain,
    db,
)

SEASON_START = date(2026, 4, 4)


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_uid"] = "test-admin"
        sess["user_role"] = "admin"
    return client


def test_delete_team_with_dependent_rows(app):
    """Deleting a team cascades away every row that FKs to it or its fixtures."""
    league = League(name="Men's League", start_date=SEASON_START)
    db.session.add(league)
    db.session.commit()

    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()

    home = Team(name="Home", division_id=division.id)
    away = Team(name="Away", division_id=division.id)
    db.session.add_all([home, away])
    db.session.commit()

    player = Player(
        first_name="Test",
        last_name="Player",
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(player)
    db.session.commit()
    home.players.append(player)
    db.session.commit()

    fixture = Fixture(
        date=SEASON_START, league_id=league.id, home_team_id=home.id, away_team_id=away.id
    )
    db.session.add(fixture)
    db.session.commit()

    rubber = Rubber(fixture_id=fixture.id, rubber_number=1, winner="home")
    db.session.add(rubber)
    db.session.commit()
    db.session.add(
        PlayerMatchResult(player_id=player.id, rubber_id=rubber.id, side="home", won=True)
    )

    db.session.add(FixtureSquadEntry(fixture_id=fixture.id, player_id=player.id, team_id=home.id))
    db.session.add(MatchFeePaid(fixture_id=fixture.id, player_id=player.id, team_id=home.id))
    db.session.add(MatchDayDraft(fixture_id=fixture.id, status="draft"))
    db.session.add(PlayerTeamCommitment(player_id=player.id, team_id=home.id, league_id=league.id))
    db.session.add(TeamCaptain(player_id=player.id, team_id=home.id))
    db.session.commit()

    home_id, fixture_id, rubber_id = home.id, fixture.id, rubber.id

    client = _admin_client(app)
    resp = client.post(f"/teams/{home_id}/delete")
    assert resp.status_code == 302

    assert Team.query.get(home_id) is None
    assert Fixture.query.get(fixture_id) is None
    assert Rubber.query.get(rubber_id) is None
    assert PlayerMatchResult.query.filter_by(player_id=player.id).count() == 0
    assert FixtureSquadEntry.query.filter_by(team_id=home_id).count() == 0
    assert MatchFeePaid.query.filter_by(team_id=home_id).count() == 0
    assert MatchDayDraft.query.filter_by(fixture_id=fixture_id).count() == 0
    assert PlayerTeamCommitment.query.filter_by(team_id=home_id).count() == 0
    assert TeamCaptain.query.filter_by(team_id=home_id).count() == 0
    # The other team in the fixture is untouched.
    assert Team.query.get(away.id) is not None
