"""Regression tests for manual_fixture_entry's Cleveland Autumn pairs plan.

manual_fixture_entry.html used to render every rubber as a generic, freely
orderable "Rubber #N" card with no concept of the pairs-rotation stride the
rest of the app relies on (rubber_number = pair_index*n_pairs + round, see
build_rubber_plan). An admin transcribing a paper scoresheet in the natural
chronological order it was played (round 1 for every pair, then round 2 for
every pair) rather than the required pair-major stride would silently
cross-attribute rubbers between pairs, with no error or warning — this is
exactly the "Opposition 1 not flipped correctly" bug reported in production
for a Yarm C vs Yarm D fixture entered directly through this form (not via
pitchside, where a separate but related bug was found and fixed).

The fix makes the GET response carry a per-league (and, for the pitchside-
confirm path, per-fixture) rubber plan so the template can auto-generate
correctly labelled, order-locked "Pair X vs Opposition N" cards instead of
leaving the order up to the admin. These tests cover the plan the route now
hands to the template — see app/__init__.py's _autumn_pairs_plan.
"""

import json
from datetime import date

from app.models import LEAGUE_FORMAT_NE_WINTER, Division, Fixture, League, Team, db

SEASON_START = date(2026, 4, 4)


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_uid"] = "test-admin"
        sess["user_role"] = "admin"
    return client


def _extract_json_const(body, const_name):
    marker = f"const {const_name} = "
    start = body.find(marker)
    assert start != -1, f"{const_name} not found in response"
    start += len(marker)
    end = body.find(";\n", start)
    return json.loads(body[start:end])


def test_new_fixture_form_includes_pairs_plan_for_autumn_multi_pair_league(app):
    """New fixture form includes pairs plan for autumn multi pair league."""
    league = League(name="Autumn League", start_date=SEASON_START, pairs_per_round=2)
    db.session.add(league)
    db.session.commit()

    client = _admin_client(app)
    resp = client.get("/fixtures/manual")
    assert resp.status_code == 200
    body = resp.data.decode()

    plans = _extract_json_const(body, "LEAGUE_PAIRS_PLANS")
    plan = plans[str(league.id)]
    assert plan["n_pairs"] == 2
    # Pair-major stride, matching build_rubber_plan()'s rubber_number = i*n_pairs+n.
    assert [(slot["rubber_number"], slot["label"]) for slot in plan["plan"]] == [
        (1, "Pair 1 vs Opposition 1"),
        (2, "Pair 1 vs Opposition 2"),
        (3, "Pair 2 vs Opposition 1"),
        (4, "Pair 2 vs Opposition 2"),
    ]


def test_new_fixture_form_omits_plan_for_fixed_squad_and_single_pair_leagues(app):
    """Fixed-squad and single-pair leagues must not get an auto-structured plan.

    No pairs-rotation ambiguity exists for a fixed-squad format (Veterans/NE
    Winter — a static discipline list, not a rotation) or a single-pair
    league (n_pairs=1 has only one physical ordering), so neither should get
    a plan — the freeform "Add Rubber" flow stays correct for them and
    shouldn't be disabled.
    """
    fixed_squad_league = League(
        name="Veterans League",
        start_date=SEASON_START,
        pairs_per_round=2,
        format=LEAGUE_FORMAT_NE_WINTER,
    )
    single_pair_league = League(name="Singles League", start_date=SEASON_START, pairs_per_round=1)
    db.session.add_all([fixed_squad_league, single_pair_league])
    db.session.commit()

    client = _admin_client(app)
    resp = client.get("/fixtures/manual")
    assert resp.status_code == 200
    plans = _extract_json_const(resp.data.decode(), "LEAGUE_PAIRS_PLANS")

    assert str(fixed_squad_league.id) not in plans
    assert str(single_pair_league.id) not in plans


def test_confirm_form_includes_pairs_plan_matching_fixtures_effective_league(app):
    """Confirm form includes pairs plan matching fixtures effective league."""
    league = League(name="Autumn League", start_date=SEASON_START, pairs_per_round=2)
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

    client = _admin_client(app)
    resp = client.get("/fixtures/manual", query_string={"fixture_id": fixture.id})
    assert resp.status_code == 200
    body = resp.data.decode()

    confirm_plan = _extract_json_const(body, "CONFIRM_PAIRS_PLAN")
    assert confirm_plan["n_pairs"] == 2
    assert [slot["rubber_number"] for slot in confirm_plan["plan"]] == [1, 2, 3, 4]
    assert [slot["label"] for slot in confirm_plan["plan"]] == [
        "Pair 1 vs Opposition 1",
        "Pair 1 vs Opposition 2",
        "Pair 2 vs Opposition 1",
        "Pair 2 vs Opposition 2",
    ]


def test_manual_entry_in_canonical_pair_major_order_lands_on_correct_rubbers(app):
    """End-to-end: canonical pair-major submission order lands correctly.

    Submitting rubbers in the pair-major stride the auto-structured form now
    enforces (Pair 1's rounds, then Pair 2's rounds — not the natural
    round-by-round order a scoresheet is played in) must produce a fixture
    whose away-perspective Pairs grid shows each pair's own, non-swapped
    results — the same invariant test_autumn_two_pairs.py already verifies
    for save_rubbers, now verified for manual_fixture_entry too.
    """
    league = League(name="Autumn League", start_date=SEASON_START, pairs_per_round=2)
    db.session.add(league)
    db.session.commit()
    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()
    home = Team(name="Yarm C", division_id=division.id)
    away = Team(name="Yarm D", division_id=division.id)
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
            # Pair 1 (rubber_number 1, 2): home wins every set 6-0.
            "rubber_0_home_set1": "6",
            "rubber_0_away_set1": "0",
            "rubber_0_home_players": "H1 One, H1 Two",
            "rubber_0_away_players": "A1 One, A1 Two",
            "rubber_1_home_set1": "6",
            "rubber_1_away_set1": "0",
            "rubber_1_home_players": "H1 One, H1 Two",
            "rubber_1_away_players": "A2 One, A2 Two",
            # Pair 2 (rubber_number 3, 4): away wins every set 6-0, so pair 1
            # and pair 2's results are unmistakably distinct.
            "rubber_2_home_set1": "0",
            "rubber_2_away_set1": "6",
            "rubber_2_home_players": "H2 One, H2 Two",
            "rubber_2_away_players": "A1 One, A1 Two",
            "rubber_3_home_set1": "0",
            "rubber_3_away_set1": "6",
            "rubber_3_home_players": "H2 One, H2 Two",
            "rubber_3_away_players": "A2 One, A2 Two",
        },
    )
    assert resp.status_code == 302

    fixture = Fixture.query.filter_by(league_id=league.id).first()

    detail_resp = client.get(f"/fixtures/{fixture.id}", query_string={"team_id": home.id})
    body = detail_resp.data.decode()

    # Pair 1's row: both "Opposition" cells show home winning 6-0.
    idx = body.find('name="rubber_0_opp1_yarm_set1"')
    assert 'value="6"' in body[idx : idx + 200]
    idx = body.find('name="rubber_0_opp2_yarm_set1"')
    assert 'value="6"' in body[idx : idx + 200]

    # Pair 2's row: both "Opposition" cells show home losing 0-6 — not one of
    # pair 1's 6-0 results bleeding across from the cross-attribution bug.
    idx = body.find('name="rubber_1_opp1_yarm_set1"')
    assert 'value="0"' in body[idx : idx + 200]
    idx = body.find('name="rubber_1_opp2_yarm_set1"')
    assert 'value="0"' in body[idx : idx + 200]
