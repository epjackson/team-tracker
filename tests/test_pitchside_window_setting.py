"""The Match Day (pitchside) portal window is admin-configurable.

Via /admin/settings (AppSetting key "pitchside_window_days"), defaulting to
10 days. It bounds which fixtures are listed for everyone; within that
window, admins (sees_all) see every fixture while captains see only
fixtures for team(s) they captain.
"""

from datetime import date, timedelta

from app.models import (
    AppSetting,
    Division,
    Fixture,
    League,
    Player,
    Team,
    TeamCaptain,
    db,
)

TODAY = date(2026, 8, 29)
SEASON_START = date(2026, 4, 4)


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_uid"] = "test-admin"
        sess["user_role"] = "admin"
    return client


def _make_team_and_captain():
    league = League(name="Autumn League", start_date=SEASON_START)
    db.session.add(league)
    db.session.commit()
    division = Division(name="Division 1", league_id=league.id)
    db.session.add(division)
    db.session.commit()
    home = Team(name="Home", division_id=division.id)
    away = Team(name="Away", division_id=division.id)
    db.session.add_all([home, away])
    db.session.commit()
    captain = Player(
        first_name="Cap",
        last_name="Tain",
        gender="M",
        membership_status="active",
        interest_team_play="yes",
    )
    db.session.add(captain)
    db.session.commit()
    home.players.append(captain)
    db.session.add(TeamCaptain(player_id=captain.id, team_id=home.id, season="2026"))
    db.session.commit()
    return home, away, captain


def _fixture_on(home, away, on_date):
    fixture = Fixture(date=on_date, home_team_id=home.id, away_team_id=away.id)
    db.session.add(fixture)
    db.session.commit()
    return fixture


def _pitchside_client(app, player_id):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["pitchside_player_id"] = player_id
    return client


def test_admin_settings_defaults_to_ten_days(app):
    """Admin settings defaults to ten days."""
    client = _admin_client(app)
    resp = client.get("/admin/settings")
    assert b'value="10"' in resp.data


def test_admin_settings_saves_custom_window(app):
    """Admin settings saves custom window."""
    client = _admin_client(app)
    resp = client.post("/admin/settings", data={"pitchside_window_days": "3"})
    assert resp.status_code == 302
    setting = AppSetting.query.filter_by(key="pitchside_window_days").first()
    assert setting.value == "3"
    assert b'value="3"' in client.get("/admin/settings").data


def test_admin_settings_rejects_out_of_range_value(app):
    """Admin settings rejects out of range value."""
    client = _admin_client(app)
    client.post("/admin/settings", data={"pitchside_window_days": "5"})
    resp = client.post("/admin/settings", data={"pitchside_window_days": "999"})
    assert resp.status_code == 302
    # The earlier valid value is untouched.
    setting = AppSetting.query.filter_by(key="pitchside_window_days").first()
    assert setting.value == "5"


def test_admin_settings_rejects_non_numeric_value(app):
    """Admin settings rejects non numeric value."""
    client = _admin_client(app)
    client.post("/admin/settings", data={"pitchside_window_days": "5"})
    client.post("/admin/settings", data={"pitchside_window_days": "abc"})
    setting = AppSetting.query.filter_by(key="pitchside_window_days").first()
    assert setting.value == "5"


def test_captain_sees_only_captained_team_fixtures_within_configured_window(app, monkeypatch):
    """Captain sees only captained team fixtures within configured window."""
    import app as app_module

    monkeypatch.setattr(app_module, "date", _FixedDate)
    home, away, captain = _make_team_and_captain()
    admin = _admin_client(app)
    admin.post("/admin/settings", data={"pitchside_window_days": "3"})

    in_window = _fixture_on(home, away, TODAY + timedelta(days=2))
    out_of_window = _fixture_on(home, away, TODAY + timedelta(days=4))
    other_team_home = Team(name="OtherHome", division_id=home.division_id)
    other_team_away = Team(name="OtherAway", division_id=home.division_id)
    db.session.add_all([other_team_home, other_team_away])
    db.session.commit()
    not_captained = _fixture_on(other_team_home, other_team_away, TODAY + timedelta(days=1))

    client = _pitchside_client(app, captain.id)
    resp = client.get("/pitchside/fixtures")
    assert resp.status_code == 200
    body = resp.data.decode()
    # In-window fixture for the captained team is listed by its date.
    assert in_window.date.strftime("%A %d %B") in body
    # Out-of-window fixture (day 4, beyond the configured 3-day window) is excluded.
    assert out_of_window.date.strftime("%A %d %B") not in body
    # In-window fixture for a team this player doesn't captain is excluded.
    assert "OtherHome" not in body
    assert not_captained.id  # sanity: the excluded fixture was actually created


class _FixedDate(date):
    """A date subclass whose today() is pinned, for deterministic window tests."""

    @classmethod
    def today(cls):
        return TODAY
