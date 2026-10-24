"""manual_fixture_entry must score every league format like save_rubbers.

Not its own default 1-pt-per-rubber-win convention — this matters because
it's also the pitchside confirm step for all formats.
"""

from datetime import date

from app.models import LEAGUE_FORMAT_NE_WINTER, Division, Fixture, League, Team, db

SEASON_START = date(2026, 4, 4)


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_uid"] = "test-admin"
        sess["user_role"] = "admin"
    return client


def test_manual_entry_ne_winter_uses_set_points_scoring(app):
    """Manual entry ne winter uses set points scoring."""
    league = League(
        name="NE Winter Manual", start_date=SEASON_START, format=LEAGUE_FORMAT_NE_WINTER
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

    client = _admin_client(app)
    resp = client.post(
        "/fixtures/manual",
        data={
            "fixture_date": "2026-04-04",
            "league_id": str(league.id),
            "home_team_id": str(home.id),
            "away_team_id": str(away.id),
            "rubber_count": "4",
            # Rubber 0 (Mens Doubles): home wins both sets -> 2-0 in set points.
            "rubber_0_home_set1": "6",
            "rubber_0_away_set1": "2",
            "rubber_0_home_set2": "6",
            "rubber_0_away_set2": "3",
            "rubber_0_home_players": "Man One, Man Two",
            "rubber_0_away_players": "Opp One, Opp Two",
            # Rubber 1 (Womens Doubles): split 1-1.
            "rubber_1_home_set1": "6",
            "rubber_1_away_set1": "2",
            "rubber_1_home_set2": "3",
            "rubber_1_away_set2": "6",
            "rubber_1_home_players": "Woman One, Woman Two",
            "rubber_1_away_players": "OppW One, OppW Two",
            # Rubber 2 (Mixed Pair 1): away wins both sets.
            "rubber_2_home_set1": "2",
            "rubber_2_away_set1": "6",
            "rubber_2_home_set2": "3",
            "rubber_2_away_set2": "6",
            "rubber_2_home_players": "Man One, Woman One",
            "rubber_2_away_players": "Opp One, OppW One",
            # Rubber 3 (Mixed Pair 2): home walkover.
            "rubber_3_walkover": "home",
            "rubber_3_home_players": "Man Two, Woman Two",
            "rubber_3_away_players": "Opp Two, OppW Two",
        },
    )
    assert resp.status_code == 302

    fixture = Fixture.query.filter_by(league_id=league.id).first()
    # If the route's own 1-pt-per-rubber-win convention had been used instead,
    # this would be 2-1 (2 home wins, 1 away win, 1 tie counted as no-score).
    # The correct set-points total is 2 (r0) + 1 (r1) + 0 (r2) + 2 (r3 w/o) = 5
    # home, and 0 + 1 + 2 + 0 = 3 away.
    assert (fixture.home_score, fixture.away_score) == (5, 3)


def test_manual_entry_cleveland_autumn_uses_rubber_results_scoring(app):
    """Autumn must score like Summer, not the route's own default.

    2 pts/rubber win, 4 bonus pts to the fixture winner (split 2-2 on a tie)
    — not the route's own 1-pt-per-win default. Home wins 3 of 4 rubbers
    here, so score is (3*2 + 4) - (1*2) = 10-2, not the naive 3-1.
    """
    league = League(name="Autumn Manual", start_date=SEASON_START)
    db.session.add(league)
    db.session.commit()
    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()
    home = Team(name="Home", division_id=division.id)
    away = Team(name="Away", division_id=division.id)
    db.session.add_all([home, away])
    db.session.commit()

    client = _admin_client(app)
    resp = client.post(
        "/fixtures/manual",
        data={
            "fixture_date": "2026-04-04",
            "league_id": str(league.id),
            "home_team_id": str(home.id),
            "away_team_id": str(away.id),
            "rubber_count": "4",
            "rubber_0_home_set1": "6",
            "rubber_0_away_set1": "2",
            "rubber_0_home_players": "Man One, Man Two",
            "rubber_0_away_players": "Opp One, Opp Two",
            "rubber_1_home_set1": "6",
            "rubber_1_away_set1": "2",
            "rubber_1_home_players": "Man One, Man Two",
            "rubber_1_away_players": "Opp Three, Opp Four",
            "rubber_2_home_set1": "6",
            "rubber_2_away_set1": "2",
            "rubber_2_home_players": "Man Three, Man Four",
            "rubber_2_away_players": "Opp One, Opp Two",
            "rubber_3_home_set1": "2",
            "rubber_3_away_set1": "6",
            "rubber_3_home_players": "Man Three, Man Four",
            "rubber_3_away_players": "Opp Three, Opp Four",
        },
    )
    assert resp.status_code == 302

    fixture = Fixture.query.filter_by(league_id=league.id).first()
    assert (fixture.home_score, fixture.away_score) == (10, 2)
