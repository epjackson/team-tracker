"""Regression tests for the Cleveland Summer league's real configuration.

9 rubbers (3 pairs, full round-robin), each a single set to 8 games — no
second set, no champion's tiebreak. Summer shares the same cleveland_autumn
`format` as Autumn but a different `sets_per_rubber` (1 vs 2) cascaded from
its ParentLeague, which is what must actually drive whether a rubber shows a
2nd set / tiebreak ("3rd set") input — previously every cleveland_autumn
rubber got a hardcoded 2-sets-plus-tiebreak shape regardless of this.
"""

from datetime import date

from app.models import Division, Fixture, League, ParentLeague, Player, Rubber, Team, db

SEASON_START = date(2026, 4, 4)


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_uid"] = "test-admin"
        sess["user_role"] = "admin"
    return client


def _player(first, last):
    p = Player(
        first_name=first,
        last_name=last,
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(p)
    return p


def _make_summer_fixture():
    parent = ParentLeague.query.filter_by(season_type="summer").first()
    parent.pairs_per_round = 3
    parent.sets_per_rubber = 1
    parent.games_per_set = 8
    parent.tiebreak_at_games = 8
    db.session.commit()

    league = League(
        name="Cleveland Summer League 2026",
        start_date=SEASON_START,
        pairs_per_round=3,
        format="cleveland_autumn",
        parent_league_id=parent.id,
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
    fixture = Fixture(
        date=SEASON_START, league_id=league.id, home_team_id=home.id, away_team_id=away.id
    )
    db.session.add(fixture)
    db.session.commit()
    return league, home, away, fixture


def test_summer_fixture_has_nine_rubbers_of_one_set(app):
    """Summer fixture has nine rubbers of one set."""
    league, home, away, fixture = _make_summer_fixture()
    p1, p2 = _player("Home", "One"), _player("Home", "Two")
    db.session.commit()

    client = _admin_client(app)
    form = {
        "team_id": str(home.id),
        "rubber_count": "3",
        "rubber_0_home_player_1": str(p1.id),
        "rubber_0_home_player_2": str(p2.id),
        "rubber_0_opp1_yarm_set1": "8",
        "rubber_0_opp1_opp_set1": "5",
    }
    resp = client.post(f"/fixtures/{fixture.id}/rubbers/save", data=form)
    assert resp.status_code == 302

    rubbers = fixture.rubbers.order_by(Rubber.rubber_number).all()
    assert len(rubbers) == 9

    r1 = rubbers[0]
    assert (r1.home_set1, r1.away_set1) == (8, 5)
    # No second set / champion tiebreak exists for a single-set rubber.
    assert r1.home_set2 is None and r1.away_set2 is None
    assert r1.home_set3 is None and r1.away_set3 is None
    assert r1.winner == "home"


def test_summer_fixture_detail_shows_only_one_set_no_tiebreak(app):
    """Summer fixture detail shows only one set no tiebreak."""
    league, home, away, fixture = _make_summer_fixture()
    client = _admin_client(app)

    resp = client.get(f"/fixtures/{fixture.id}", query_string={"team_id": home.id})
    assert resp.status_code == 200
    body = resp.data.decode()

    # 3 opposition columns (3 pairs), not the 9-rubber default's leftover
    # assumption of anything else.
    assert "Opposition 1" in body
    assert "Opposition 2" in body
    assert "Opposition 3" in body

    # Exactly one set of score inputs per rubber — no 2nd-set or
    # champion's-tiebreak ("3rd set") inputs anywhere on the page.
    assert 'name="rubber_0_opp1_yarm_set1"' in body
    assert 'name="rubber_0_opp1_yarm_set2"' not in body
    assert 'name="rubber_0_opp1_yarm_set3"' not in body

    # Games are raced to 8, so the score input allows up to 9 (an 8-8
    # decider), not the Autumn 2-set format's 7.
    idx = body.find('name="rubber_0_opp1_yarm_set1"')
    assert 'max="9"' in body[idx : idx + 200]
