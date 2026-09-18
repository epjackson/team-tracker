"""Regression tests for a Cleveland Autumn league with pairs_per_round=2.

This is the real club config — real Autumn leagues use 2 pairs per side, not
the historical default of 3. Cleveland Autumn is a genuine round-robin: every
one of the n_pairs home pairs plays every one of the n_pairs away pairs
exactly once, so there are always exactly n_pairs rounds (and n_pairs^2
rubbers) — not a fixed 3 rounds, which only ever coincided with reality for
the historical 3-pairs-per-side default. With pairs_per_round=2 that means 2
rounds and 4 rubbers, at a storage stride of n_pairs (rubber_number =
pair_index*n_pairs + round). Both fixture_detail.html's rubber grid and
save_rubbers' away-perspective branch must derive counts/strides from
n_pairs, not a hardcoded 3.
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


def _make_fixture(pairs_per_round=2):
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


def test_two_pairs_produce_exactly_four_rubbers_not_six(app):
    """A genuine 2-pairs-a-side round-robin needs only 2 rounds, 4 rubbers.

    Each home pair meets each away pair once — 4 rubbers total, not 6 (which
    a fixed-3-rounds assumption would produce, with one opponent faced
    twice).
    """
    league, home, away, fixture = _make_fixture(pairs_per_round=2)
    p1, p2, p3, p4 = (
        _player("Home", "One"),
        _player("Home", "Two"),
        _player("Home", "Three"),
        _player("Home", "Four"),
    )
    db.session.commit()

    client = _admin_client(app)
    form = {
        "team_id": str(home.id),
        "rubber_count": "2",
        "rubber_0_home_player_1": str(p1.id),
        "rubber_0_home_player_2": str(p2.id),
        "rubber_1_home_player_1": str(p3.id),
        "rubber_1_home_player_2": str(p4.id),
    }
    for i in range(2):
        for n in (1, 2):
            form[f"rubber_{i}_opp{n}_yarm_set1"] = "6"
            form[f"rubber_{i}_opp{n}_opp_set1"] = "0"
            form[f"rubber_{i}_opp{n}_yarm_set2"] = "6"
            form[f"rubber_{i}_opp{n}_opp_set2"] = "0"

    resp = client.post(f"/fixtures/{fixture.id}/rubbers/save", data=form)
    assert resp.status_code == 302

    rubbers = {r.rubber_number: r for r in fixture.rubbers.all()}
    assert len(rubbers) == 4
    assert set(rubbers) == {1, 2, 3, 4}


def test_home_perspective_second_pair_addresses_its_own_rubbers(app):
    """Pair 2's "Opposition 2" cell must map to rubber_number 4 (1*2+2).

    It must not overlap with Pair 1's rubbers (1, 2) — and its champion's
    tiebreak ("3rd set") must round-trip back onto the page correctly.
    """
    league, home, away, fixture = _make_fixture(pairs_per_round=2)
    p1, p2, p3, p4 = (
        _player("Home", "One"),
        _player("Home", "Two"),
        _player("Home", "Three"),
        _player("Home", "Four"),
    )
    db.session.commit()

    client = _admin_client(app)
    form = {
        "team_id": str(home.id),
        "rubber_count": "2",
        "rubber_0_home_player_1": str(p1.id),
        "rubber_0_home_player_2": str(p2.id),
        "rubber_1_home_player_1": str(p3.id),
        "rubber_1_home_player_2": str(p4.id),
        "rubber_0_opp1_yarm_set1": "6",
        "rubber_0_opp1_opp_set1": "0",
        "rubber_0_opp1_yarm_set2": "6",
        "rubber_0_opp1_opp_set2": "0",
        "rubber_0_opp2_yarm_set1": "6",
        "rubber_0_opp2_opp_set1": "0",
        "rubber_0_opp2_yarm_set2": "6",
        "rubber_0_opp2_opp_set2": "0",
        "rubber_1_opp1_yarm_set1": "6",
        "rubber_1_opp1_opp_set1": "0",
        "rubber_1_opp1_yarm_set2": "6",
        "rubber_1_opp1_opp_set2": "0",
        # Pair 2 (i=1) vs Opposition 2 (n=2) gets a distinctive split-sets
        # result plus a champion's tiebreak, so it's unmistakable which
        # physical rubber it landed on.
        "rubber_1_opp2_yarm_set1": "6",
        "rubber_1_opp2_opp_set1": "4",
        "rubber_1_opp2_yarm_set2": "4",
        "rubber_1_opp2_opp_set2": "6",
        "rubber_1_opp2_yarm_set3": "10",
        "rubber_1_opp2_opp_set3": "7",
    }

    resp = client.post(f"/fixtures/{fixture.id}/rubbers/save", data=form)
    assert resp.status_code == 302

    rubbers = {r.rubber_number: r for r in fixture.rubbers.all()}
    assert len(rubbers) == 4

    r4 = rubbers[4]
    assert (r4.home_set1, r4.away_set1, r4.home_set2, r4.away_set2) == (6, 4, 4, 6)
    assert (r4.home_set3, r4.away_set3) == (10, 7)

    # Pair 1's rubbers (1, 2) must be untouched by pair 2's split-set entry.
    for rn in (1, 2, 3):
        r = rubbers[rn]
        assert (r.home_set1, r.away_set1, r.home_set2, r.away_set2) == (6, 0, 6, 0)
        assert r.home_set3 is None and r.away_set3 is None

    # The champion-tiebreak ("3rd set") inputs for pair 2's opp2 rubber must
    # round-trip back onto the page, pre-filled with the saved 10-7 score.
    detail_resp = client.get(f"/fixtures/{fixture.id}", query_string={"team_id": home.id})
    assert detail_resp.status_code == 200
    body = detail_resp.data.decode()
    idx = body.find('name="rubber_1_opp2_yarm_set3"')
    assert idx != -1
    assert 'value="10"' in body[idx : idx + 200]


def test_away_perspective_two_pairs_land_on_correct_non_overlapping_rubbers(app):
    """The away side's pair submissions must attach to the correct rubbers.

    They must attach to exactly the rubbers the round-robin rotation assigns
    them, matching the home side's own (never-buggy) numbering — otherwise a
    dual-Yarm fixture's independently-submitted home scores and away player
    names merge onto the wrong rows. Each home pair's rubbers are tagged with
    a distinctive marker score (home_set1 = rubber_number) so we can tell
    exactly which physical rubber an away pair's players ended up on.
    """
    league, home, away, fixture = _make_fixture(pairs_per_round=2)
    hp1a, hp1b = _player("HomePair1", "A"), _player("HomePair1", "B")
    hp2a, hp2b = _player("HomePair2", "A"), _player("HomePair2", "B")
    ap1a, ap1b = _player("AwayPair1", "A"), _player("AwayPair1", "B")
    ap2a, ap2b = _player("AwayPair2", "A"), _player("AwayPair2", "B")
    db.session.commit()

    client = _admin_client(app)

    # Home enters pair 0 and pair 1, each rubber marked with its own
    # rubber_number as the home_set1 score (1..4).
    home_form = {
        "team_id": str(home.id),
        "rubber_count": "2",
        "rubber_0_home_player_1": str(hp1a.id),
        "rubber_0_home_player_2": str(hp1b.id),
        "rubber_1_home_player_1": str(hp2a.id),
        "rubber_1_home_player_2": str(hp2b.id),
    }
    for i in range(2):
        for n in (1, 2):
            home_form[f"rubber_{i}_opp{n}_yarm_set1"] = str(i * 2 + n)
            home_form[f"rubber_{i}_opp{n}_opp_set1"] = "0"
    resp = client.post(f"/fixtures/{fixture.id}/rubbers/save", data=home_form)
    assert resp.status_code == 302

    # Away separately submits its own 2 pairs' player names, with no score
    # fields at all — only the pairing rotation should decide which physical
    # rubbers get these PlayerMatchResult rows.
    away_form = {
        "team_id": str(away.id),
        "rubber_count": "2",
        "rubber_0_home_player_1": str(ap1a.id),
        "rubber_0_home_player_2": str(ap1b.id),
        "rubber_1_home_player_1": str(ap2a.id),
        "rubber_1_home_player_2": str(ap2b.id),
    }
    resp = client.post(f"/fixtures/{fixture.id}/rubbers/save", data=away_form)
    assert resp.status_code == 302

    def markers_for(player_ids):
        markers = set()
        for rn in range(1, 5):
            rubber = Rubber.query.filter_by(fixture_id=fixture.id, rubber_number=rn).first()
            names = {
                pr.player_id
                for pr in PlayerMatchResult.query.filter_by(rubber_id=rubber.id, side="away").all()
            }
            if names == player_ids:
                markers.add(rubber.home_set1)
        return markers

    # Away pair 0 faces home pair 0 (round 1, rubber 1) and home pair 1
    # (round 2, rubber 4) via oppIndex(0, n) = (0-n+1) % 2.
    assert markers_for({ap1a.id, ap1b.id}) == {1, 4}
    # Away pair 1 faces home pair 1 (round 1, rubber 3) and home pair 0
    # (round 2, rubber 2) via oppIndex(1, n) = (1-n+1) % 2.
    assert markers_for({ap2a.id, ap2b.id}) == {2, 3}
