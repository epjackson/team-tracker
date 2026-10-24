"""Opposition players must never be turned into real Player records.

An untracked, external side of a fixture (no Team row, just a free-text
home_team_name/away_team_name) must never get real Player records created
for it, whether a captain confirms a Match Day scoresheet or an admin fills
in a fixture manually — see CLAUDE.md's "Player name lookups" and the
pitchside portal notes: only "my team" gets name suggestions, the opposition
side is free text for on-screen reference only.
"""

from datetime import date

from app.models import (
    Division,
    Fixture,
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


def test_confirm_fixture_does_not_create_players_for_untracked_away_side(app):
    """Confirm fixture does not create players for untracked away side."""
    league = League(name="Autumn Confirm", start_date=SEASON_START)
    db.session.add(league)
    db.session.commit()
    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()
    home = Team(name="Home", division_id=division.id)
    db.session.add(home)
    db.session.commit()

    # Mirrors ICS import for a fixture against an external club: our own team
    # is tracked, the opponent is name-only (away_team_id is None).
    fixture = Fixture(
        date=SEASON_START,
        league_id=league.id,
        home_team_id=home.id,
        away_team_id=None,
        away_team_name="Opponent Club",
    )
    db.session.add(fixture)
    db.session.commit()
    db.session.add(Rubber(fixture_id=fixture.id, rubber_number=1, rubber_type="doubles"))
    db.session.commit()

    players_before = Player.query.count()

    client = _admin_client(app)
    resp = client.post(
        "/fixtures/manual",
        data={
            "fixture_id": str(fixture.id),
            "rubber_count": "1",
            "rubber_0_home_set1": "6",
            "rubber_0_away_set1": "2",
            "rubber_0_home_players": "Man One, Man Two",
            "rubber_0_away_players": "BC1a, BC1b",
        },
    )
    assert resp.status_code == 302

    # Only the two home (our club's) players were created.
    assert Player.query.count() == players_before + 2
    assert Player.query.filter(Player.first_name == "BC1a").first() is None
    assert Player.query.filter(Player.first_name == "BC1b").first() is None

    rubber = fixture.rubbers.first()
    results = PlayerMatchResult.query.filter_by(rubber_id=rubber.id).all()
    assert {r.side for r in results} == {"home"}
    assert len(results) == 2
