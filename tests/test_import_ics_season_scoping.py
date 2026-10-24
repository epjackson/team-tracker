"""Regression tests for season-scoping on the /fixtures/import-ics page.

A Team is tied to one Division, which is tied to one League/season, so
identically-named teams in different seasons (e.g. "Yarm A Men" in both Summer
and Autumn) are always independent Team rows — see CLAUDE.md. Before this fix,
import_ics listed every club team/league regardless of the selected season
badge, and matched opponent teams club-wide, so the wrong season's team could
be selected or silently auto-matched.
"""

import io
import re
from datetime import date

from app.models import Division, League, ParentLeague, Team, db

SEASON_START = date(2026, 4, 4)


def _make_season_league(name, season_type, start_date=SEASON_START):
    """Create a League under a fresh ParentLeague tagged with season_type."""
    parent = ParentLeague(
        name=f"{name} Parent",
        season_type=season_type,
        pairs_per_round=3,
    )
    db.session.add(parent)
    db.session.flush()
    league = League(name=name, start_date=start_date, parent_league_id=parent.id)
    db.session.add(league)
    db.session.commit()
    return league


def _make_division(league, name="Division 1"):
    division = Division(name=name, league_id=league.id)
    db.session.add(division)
    db.session.commit()
    return division


def _make_team(name, division=None):
    team = Team(name=name, division_id=division.id if division else None)
    db.session.add(team)
    db.session.commit()
    return team


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_uid"] = "test-admin"
        sess["user_role"] = "admin"
    return client


def test_import_ics_form_scopes_teams_and_leagues_to_selected_season(app):
    """The Import from Calendar form only lists the selected season's leagues/teams."""
    autumn = _make_season_league("Cleveland Mens Autumn League 2026", "autumn")
    summer = _make_season_league("Cleveland Mens Summer League 2026", "summer")
    autumn_team = _make_team("Yarm A Men", _make_division(autumn, "Autumn Div 1"))
    summer_team = _make_team("Yarm A Men", _make_division(summer, "Summer Div 1"))

    client = _admin_client(app)

    # All Seasons (nothing selected): both leagues and both teams are offered.
    resp = client.get("/fixtures/import-ics")
    assert autumn.name.encode() in resp.data
    assert summer.name.encode() in resp.data
    assert f'value="{autumn_team.id}" data-league-id'.encode() in resp.data
    assert f'value="{summer_team.id}" data-league-id'.encode() in resp.data

    # Autumn badge selected: only the Autumn league and Autumn team appear.
    client.get("/select-season?season_type=autumn")
    resp = client.get("/fixtures/import-ics")
    assert autumn.name.encode() in resp.data
    assert summer.name.encode() not in resp.data
    assert f'value="{autumn_team.id}" data-league-id'.encode() in resp.data
    assert f'value="{summer_team.id}" data-league-id'.encode() not in resp.data


def test_import_ics_opponent_match_scoped_to_our_team_league(app):
    """The opponent team is auto-matched within our team's own league, not club-wide.

    Regression test: an identically-named team existing in both the Summer and
    Autumn leagues (e.g. "Yarm B Men") must resolve to the same-season opponent,
    not whichever one happens to be matched first in an unscoped search.
    """
    summer = _make_season_league("Cleveland Mens Summer League 2026", "summer")
    autumn = _make_season_league("Cleveland Mens Autumn League 2026", "autumn")

    # Create the Summer duplicate first so an unscoped, insertion-ordered match
    # would incorrectly resolve to it instead of the Autumn team.
    summer_opponent = _make_team("Yarm B Men", _make_division(summer, "Summer Div 1"))
    autumn_div = _make_division(autumn, "Autumn Div 1")
    autumn_us = _make_team("Yarm A Men", autumn_div)
    autumn_opponent = _make_team("Yarm B Men", autumn_div)

    ics_content = (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260901\r\n"
        "SUMMARY:Yarm A Men - Yarm B Men\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    ).encode("utf-8")

    client = _admin_client(app)
    resp = client.post(
        "/fixtures/import-ics",
        data={
            "action": "parse",
            "our_team_id": str(autumn_us.id),
            "ics_file": (io.BytesIO(ics_content), "cal.ics"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    match = re.search(r'internal_away_id_0"\s+value="(\d+)"', resp.data.decode())
    assert (
        match is not None
    ), "no internal away-team match rendered — event wasn't matched as internal"
    assert int(match.group(1)) == autumn_opponent.id
    assert int(match.group(1)) != summer_opponent.id
