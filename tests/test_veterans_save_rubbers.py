"""Route-level tests for save_rubbers against a Cleveland-Veterans-format fixture.

Particularly the dual-Yarm cross-rubber case (Mixed 1 v Mixed 2 / Mixed 2 v
Mixed 1) where the two sides field DIFFERENT one of their own two mixed
pairs — the bug the initial draft plan got wrong (see build_rubber_plan in
app/models.py).
"""

from datetime import date

from app.models import (
    LEAGUE_FORMAT_CLEVELAND_VETERANS,
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


def test_veterans_dual_yarm_cross_rubbers_use_correct_own_pair(app):
    """Veterans dual yarm cross rubbers use correct own pair."""
    league = League(
        name="Veterans League", start_date=SEASON_START, format=LEAGUE_FORMAT_CLEVELAND_VETERANS
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

    h_man1 = _player("HMan", "One", "M")
    h_man2 = _player("HMan", "Two", "M")
    h_woman1 = _player("HWoman", "One", "F")
    h_woman2 = _player("HWoman", "Two", "F")
    a_man1 = _player("AMan", "One", "M")
    a_man2 = _player("AMan", "Two", "M")
    a_woman1 = _player("AWoman", "One", "F")
    a_woman2 = _player("AWoman", "Two", "F")
    db.session.commit()

    fixture = Fixture(
        date=SEASON_START, league_id=league.id, home_team_id=home.id, away_team_id=away.id
    )
    db.session.add(fixture)
    db.session.commit()
    fixture_id = fixture.id

    client = _admin_client(app)

    # Home captain saves first, including all 6 rubbers' scores.
    home_resp = client.post(
        f"/fixtures/{fixture_id}/rubbers/save",
        data={
            "team_id": str(home.id),
            "squad_pair1_man": str(h_man1.id),
            "squad_pair1_woman": str(h_woman1.id),
            "squad_pair2_man": str(h_man2.id),
            "squad_pair2_woman": str(h_woman2.id),
            "rubber_1_home_set1": "6",
            "rubber_1_away_set1": "2",
            "rubber_2_home_set1": "6",
            "rubber_2_away_set1": "3",
            "rubber_3_home_set1": "6",
            "rubber_3_away_set1": "4",
            "rubber_4_home_set1": "6",
            "rubber_4_away_set1": "1",
            "rubber_5_home_set1": "2",
            "rubber_5_away_set1": "6",
            "rubber_6_home_set1": "3",
            "rubber_6_away_set1": "6",
        },
    )
    assert home_resp.status_code == 302

    # Away captain then saves their own squad — scores are read-only from this
    # side (mirrors the pairs-rotation away-perspective convention).
    away_resp = client.post(
        f"/fixtures/{fixture_id}/rubbers/save",
        data={
            "team_id": str(away.id),
            "squad_pair1_man": str(a_man1.id),
            "squad_pair1_woman": str(a_woman1.id),
            "squad_pair2_man": str(a_man2.id),
            "squad_pair2_woman": str(a_woman2.id),
        },
    )
    assert away_resp.status_code == 302

    rubbers = {r.rubber_number: r for r in Rubber.query.filter_by(fixture_id=fixture_id).all()}

    def _side_pids(rubber_id, side):
        return {
            r.player_id
            for r in PlayerMatchResult.query.filter_by(rubber_id=rubber_id, side=side).all()
        }

    # Rubber 3: Mixed 1 v Mixed 1 — symmetric, both sides field their pair 1.
    assert _side_pids(rubbers[3].id, "home") == {h_man1.id, h_woman1.id}
    assert _side_pids(rubbers[3].id, "away") == {a_man1.id, a_woman1.id}

    # Rubber 4: Mixed 1 v Mixed 2 — home fields pair 1, away fields pair 2.
    assert _side_pids(rubbers[4].id, "home") == {h_man1.id, h_woman1.id}
    assert _side_pids(rubbers[4].id, "away") == {a_man2.id, a_woman2.id}

    # Rubber 5: Mixed 2 v Mixed 1 — home fields pair 2, away fields pair 1.
    assert _side_pids(rubbers[5].id, "home") == {h_man2.id, h_woman2.id}
    assert _side_pids(rubbers[5].id, "away") == {a_man1.id, a_woman1.id}

    # Rubber 6: Mixed 2 v Mixed 2 — symmetric, both sides field their pair 2.
    assert _side_pids(rubbers[6].id, "home") == {h_man2.id, h_woman2.id}
    assert _side_pids(rubbers[6].id, "away") == {a_man2.id, a_woman2.id}

    # Scores/winners entered by home are untouched by the away captain's save.
    fixture = Fixture.query.get(fixture_id)
    # 4 home wins (r1,r2,r3,r4), 2 away wins (r5,r6) -> 4*2+4=12, 2*2+0=4.
    assert (fixture.home_score, fixture.away_score) == (12, 4)
