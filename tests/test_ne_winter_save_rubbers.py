"""Route-level test for save_rubbers against an NE-Winter-format fixture.

Covers the wiring the pure-function tests can't: form parsing, the fixed
4-player squad, rubber-number-keyed field names, and that fixture.home_score/
away_score end up matching score_from_set_points end to end.
"""

from datetime import date

from app.models import (
    LEAGUE_FORMAT_NE_WINTER,
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


def _player(first, last, gender):
    p = Player(
        first_name=first,
        last_name=last,
        gender=gender,
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(p)
    return p


def test_save_rubbers_ne_winter_end_to_end(app):
    """Save rubbers ne winter end to end."""
    league = League(
        name="NE Winter League", start_date=SEASON_START, format=LEAGUE_FORMAT_NE_WINTER
    )
    db.session.add(league)
    db.session.commit()

    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()

    home = Team(name="Home", division_id=division.id)
    db.session.add(home)
    db.session.commit()

    man1 = _player("Man", "One", "M")
    man2 = _player("Man", "Two", "M")
    woman1 = _player("Woman", "One", "F")
    woman2 = _player("Woman", "Two", "F")
    db.session.commit()

    fixture = Fixture(
        date=SEASON_START,
        league_id=league.id,
        home_team_id=home.id,
        away_team_name="Away Club",
    )
    db.session.add(fixture)
    db.session.commit()
    fixture_id = fixture.id

    client = _admin_client(app)
    resp = client.post(
        f"/fixtures/{fixture_id}/rubbers/save",
        data={
            "team_id": str(home.id),
            "squad_pair1_man": str(man1.id),
            "squad_pair1_woman": str(woman1.id),
            "squad_pair2_man": str(man2.id),
            "squad_pair2_woman": str(woman2.id),
            # Rubber 1: Mens Doubles — home wins both sets.
            "rubber_1_home_set1": "6",
            "rubber_1_away_set1": "2",
            "rubber_1_home_set2": "6",
            "rubber_1_away_set2": "3",
            # Rubber 2: Womens Doubles — split 1-1, a drawn rubber.
            "rubber_2_home_set1": "6",
            "rubber_2_away_set1": "2",
            "rubber_2_home_set2": "3",
            "rubber_2_away_set2": "6",
            # Rubber 3: Mixed Pair 1 — home loses both sets.
            "rubber_3_home_set1": "2",
            "rubber_3_away_set1": "6",
            "rubber_3_home_set2": "3",
            "rubber_3_away_set2": "6",
            # Rubber 4: Mixed Pair 2 — home walkover win.
            "rubber_4_walkover": "home",
        },
    )
    assert resp.status_code == 302

    fixture = Fixture.query.get(fixture_id)
    # r1: 2-0, r2: 1-1, r3: 0-2, r4 walkover: 2-0 -> totals 5-3.
    assert (fixture.home_score, fixture.away_score) == (5, 3)

    rubbers = {r.rubber_number: r for r in Rubber.query.filter_by(fixture_id=fixture_id).all()}
    assert rubbers[1].winner == "home"
    assert rubbers[2].winner == "tie"
    assert rubbers[3].winner == "away"
    assert rubbers[4].winner == "home"
    assert rubbers[4].set1_walkover == "home"

    mens_doubles_pids = {
        r.player_id for r in PlayerMatchResult.query.filter_by(rubber_id=rubbers[1].id).all()
    }
    assert mens_doubles_pids == {man1.id, man2.id}

    womens_doubles_pids = {
        r.player_id for r in PlayerMatchResult.query.filter_by(rubber_id=rubbers[2].id).all()
    }
    assert womens_doubles_pids == {woman1.id, woman2.id}

    mixed1_pids = {
        r.player_id for r in PlayerMatchResult.query.filter_by(rubber_id=rubbers[3].id).all()
    }
    assert mixed1_pids == {man1.id, woman1.id}

    mixed2_pids = {
        r.player_id for r in PlayerMatchResult.query.filter_by(rubber_id=rubbers[4].id).all()
    }
    assert mixed2_pids == {man2.id, woman2.id}


def test_save_rubbers_ne_winter_requires_full_squad(app):
    """Saving with an incomplete squad is rejected rather than silently wiping data."""
    league = League(
        name="NE Winter League 2", start_date=SEASON_START, format=LEAGUE_FORMAT_NE_WINTER
    )
    db.session.add(league)
    db.session.commit()
    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()
    home = Team(name="Home", division_id=division.id)
    db.session.add(home)
    db.session.commit()
    man1 = _player("Man", "One", "M")
    db.session.commit()

    fixture = Fixture(
        date=SEASON_START, league_id=league.id, home_team_id=home.id, away_team_name="Away Club"
    )
    db.session.add(fixture)
    db.session.commit()
    fixture_id = fixture.id

    client = _admin_client(app)
    resp = client.post(
        f"/fixtures/{fixture_id}/rubbers/save",
        data={"team_id": str(home.id), "squad_pair1_man": str(man1.id)},
    )
    assert resp.status_code == 302
    assert Rubber.query.filter_by(fixture_id=fixture_id).count() == 0
