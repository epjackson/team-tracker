"""Route-level tests for save_rubbers against a Cleveland-Autumn-format fixture.

Each rubber is 2 sets to 6 games, with a champion's tie-break to 10 deciding a
rubber whose sets split 1-1. Exercises the real /fixtures/<id>/rubbers/save
route (both the home/single-Yarm branch and the away-perspective dual-Yarm
branch) to confirm the renamed rubber_{i}_opp{n}_yarm_set*/opp_set* form
fields wire correctly into Rubber.home_set1/2/3 and away_set1/2/3, and that
rubber_winner_from_sets is applied for the winner.

Uses pairs_per_round=3 (a full round-robin: 3 rounds, 9 rubbers) so pair 0
genuinely faces 3 distinct opponent columns (opp1/opp2/opp3, rubbers 1-3) —
with fewer pairs there are correspondingly fewer rounds/opponents.
"""

from datetime import date

from app.models import Division, Fixture, League, Player, Rubber, Team, db

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


def _make_fixture(pairs_per_round=3):
    league = League(name="Autumn League", start_date=SEASON_START, pairs_per_round=pairs_per_round)
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


def test_home_perspective_straight_sets_and_champion_tiebreak(app):
    """Home perspective straight sets and champion tiebreak."""
    league, home, away, fixture = _make_fixture(pairs_per_round=3)
    p1, p2 = _player("Home", "One"), _player("Home", "Two")
    db.session.commit()

    client = _admin_client(app)
    resp = client.post(
        f"/fixtures/{fixture.id}/rubbers/save",
        data={
            "team_id": str(home.id),
            "rubber_count": "3",
            "rubber_0_home_player_1": str(p1.id),
            "rubber_0_home_player_2": str(p2.id),
            # Opponent 1: straight sets home win.
            "rubber_0_opp1_yarm_set1": "6",
            "rubber_0_opp1_opp_set1": "2",
            "rubber_0_opp1_yarm_set2": "6",
            "rubber_0_opp1_opp_set2": "3",
            # Opponent 2: sets split 1-1, champion tiebreak decides for home.
            "rubber_0_opp2_yarm_set1": "6",
            "rubber_0_opp2_opp_set1": "4",
            "rubber_0_opp2_yarm_set2": "4",
            "rubber_0_opp2_opp_set2": "6",
            "rubber_0_opp2_yarm_set3": "10",
            "rubber_0_opp2_opp_set3": "8",
            # Opponent 3: straight sets away win.
            "rubber_0_opp3_yarm_set1": "3",
            "rubber_0_opp3_opp_set1": "6",
            "rubber_0_opp3_yarm_set2": "2",
            "rubber_0_opp3_opp_set2": "6",
        },
    )
    assert resp.status_code == 302

    rubbers = fixture.rubbers.order_by(Rubber.rubber_number).all()
    assert len(rubbers) == 9
    r1, r2, r3 = rubbers[:3]

    assert (r1.home_set1, r1.away_set1, r1.home_set2, r1.away_set2) == (6, 2, 6, 3)
    assert r1.winner == "home"

    assert (r2.home_set1, r2.away_set1, r2.home_set2, r2.away_set2) == (6, 4, 4, 6)
    assert (r2.home_set3, r2.away_set3) == (10, 8)
    assert r2.winner == "home"

    assert (r3.home_set1, r3.away_set1, r3.home_set2, r3.away_set2) == (3, 6, 2, 6)
    assert r3.winner == "away"

    # 2 home wins (4 pts) + 1 away win (2 pts) + 4 bonus pts to home (more wins).
    fixture_reloaded = Fixture.query.get(fixture.id)
    assert (fixture_reloaded.home_score, fixture_reloaded.away_score) == (4 + 4, 2)

    # The rubber grid (2 sets + a populated champion-tiebreak row for opp2)
    # must render without a Jinja error.
    detail_resp = client.get(f"/fixtures/{fixture.id}", query_string={"team_id": home.id})
    assert detail_resp.status_code == 200
    assert b"rubber_0_opp2_yarm_set3" in detail_resp.data


def test_away_perspective_reads_yarm_and_opp_fields_correctly(app):
    """The away team's own yarm_set*/opp_set* fields must map correctly.

    rubber_{i}_opp{n}_yarm_set*/opp_set* (yarm is away here) must map to the
    correct true home_set/away_set columns.
    """
    league, home, away, fixture = _make_fixture(pairs_per_round=3)
    ap1, ap2 = _player("Away", "One"), _player("Away", "Two")
    db.session.commit()

    client = _admin_client(app)
    resp = client.post(
        f"/fixtures/{fixture.id}/rubbers/save",
        data={
            "team_id": str(away.id),
            "rubber_count": "3",
            "rubber_0_home_player_1": str(ap1.id),
            "rubber_0_home_player_2": str(ap2.id),
            # The walkover-hidden field is always present in a real page load
            # (empty when not a walkover) — this is what gates score processing
            # in the away-perspective branch.
            "rubber_0_opp1_walkover": "",
            # Away (yarm) wins straight sets 6-2, 6-3 against opponent 1.
            "rubber_0_opp1_yarm_set1": "6",
            "rubber_0_opp1_opp_set1": "2",
            "rubber_0_opp1_yarm_set2": "6",
            "rubber_0_opp1_opp_set2": "3",
        },
    )
    assert resp.status_code == 302

    rubbers = fixture.rubbers.order_by(Rubber.rubber_number).all()
    assert len(rubbers) == 9
    r1 = rubbers[0]
    # yarm (away) scored 6,6 — true home_set columns must hold the opponent's
    # (home team's) score, away_set columns must hold yarm's (away team's) score.
    assert (r1.home_set1, r1.away_set1, r1.home_set2, r1.away_set2) == (2, 6, 3, 6)
    assert r1.winner == "away"


def test_walkover_still_synthesizes_eight_zero_on_set1_only(app):
    """Walkover still synthesizes eight zero on set1 only."""
    league, home, away, fixture = _make_fixture(pairs_per_round=1)
    p1, p2 = _player("Home", "One"), _player("Home", "Two")
    db.session.commit()

    client = _admin_client(app)
    resp = client.post(
        f"/fixtures/{fixture.id}/rubbers/save",
        data={
            "team_id": str(home.id),
            "rubber_count": "1",
            "rubber_0_home_player_1": str(p1.id),
            "rubber_0_home_player_2": str(p2.id),
            "rubber_0_opp1_walkover": "home",
        },
    )
    assert resp.status_code == 302

    r1 = fixture.rubbers.order_by(Rubber.rubber_number).all()[0]
    assert (r1.home_set1, r1.away_set1) == (8, 0)
    assert r1.home_set2 is None and r1.home_set3 is None
    assert r1.winner == "home"
