"""Flask application factory and route definitions."""

import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import firebase_admin
from firebase_admin import auth as firebase_auth
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import case as sa_case
from sqlalchemy import func as sqlfunc
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from app.gcs_sync import get_maintenance_info, get_secret, register_commit_sync

from .models import (
    DISCIPLINE_COLOURS,
    LEAGUE_FORMAT_CHOICES,
    LEAGUE_FORMAT_CLEVELAND_AUTUMN,
    LEAGUE_FORMAT_CLEVELAND_VETERANS,
    LEAGUE_FORMAT_LABELS,
    LEAGUE_FORMAT_NE_WINTER,
    SEASON_TYPE_AUTUMN,
    SEASON_TYPE_BADGES,
    SEASON_TYPE_CHOICES,
    SEASON_TYPE_COLOURS,
    SEASON_TYPE_LABELS,
    SEASON_TYPE_SUMMER,
    SEASON_TYPE_VETERANS,
    SEASON_TYPE_WINTER,
    AppSetting,
    CaptainUser,
    Division,
    Fixture,
    FixtureSquadEntry,
    League,
    LeagueCatchupWeek,
    LeagueRestriction,
    LoginEvent,
    MatchDayDraft,
    MatchFeePaid,
    ParentLeague,
    Player,
    PlayerMatchResult,
    PlayerTeamCommitment,
    Rubber,
    Team,
    TeamCaptain,
    _division_rank,
    _league_year,
    _team_rank,
    autumn_away_rubber_number,
    build_rubber_plan,
    check_player_eligibility,
    check_week_conflict,
    current_leagues_for_season_type,
    current_season_group,
    db,
    fixture_round_label,
    fixtures_query_for_league,
    group_leagues_for_display,
    league_format_description,
    players_query_for_league,
    rubber_winner_from_sets,
    score_from_rubber_results,
    score_from_set_points,
    teams_query_for_league,
    walkover_max_score,
)


def _migrate_rubber_player_names(db):
    """Add home_player_1/home_player_2 columns to rubbers table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("rubbers"):
        return
    existing = {c["name"] for c in inspector.get_columns("rubbers")}
    with db.engine.begin() as conn:
        for col in ("home_player_1", "home_player_2"):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE rubbers ADD COLUMN {col} VARCHAR(120)"))


def _migrate_rubber_tie_columns(db):
    """Add set1_tie/set2_tie/set3_tie columns to rubbers table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("rubbers"):
        return
    existing = {c["name"] for c in inspector.get_columns("rubbers")}
    with db.engine.begin() as conn:
        for col in ("set1_tie", "set2_tie", "set3_tie"):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE rubbers ADD COLUMN {col} BOOLEAN DEFAULT 0"))


def _migrate_rubber_walkover_column(db):
    """Add set1_walkover column to rubbers table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("rubbers"):
        return
    existing = {c["name"] for c in inspector.get_columns("rubbers")}
    if "set1_walkover" not in existing:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE rubbers ADD COLUMN set1_walkover VARCHAR(10)"))


def _migrate_league_pairs_per_round(db):
    """Add pairs_per_round column to leagues table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("leagues"):
        return
    existing = {c["name"] for c in inspector.get_columns("leagues")}
    if "pairs_per_round" not in existing:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE leagues ADD COLUMN pairs_per_round INTEGER"))


def _migrate_league_parent_id(db):
    """Add parent_league_id column to leagues table if missing, and backfill.

    One-time backfill: creates "Cleveland Summer League" and points every
    existing parent-less League at it. No-op once any ParentLeague row exists.
    """
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("leagues"):
        return
    existing = {c["name"] for c in inspector.get_columns("leagues")}
    if "parent_league_id" not in existing:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE leagues ADD COLUMN parent_league_id "
                    "INTEGER REFERENCES parent_leagues (id)"
                )
            )


def _migrate_parent_league_format(db):
    """Add format column to parent_leagues table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("parent_leagues"):
        return
    existing = {c["name"] for c in inspector.get_columns("parent_leagues")}
    if "format" not in existing:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE parent_leagues ADD COLUMN format VARCHAR(30) "
                    "NOT NULL DEFAULT 'cleveland_autumn'"
                )
            )


def _migrate_parent_league_season_type(db):
    """Add season_type column to parent_leagues if missing, and best-effort backfill.

    One-time backfill: infers summer/autumn/veterans/winter from each existing
    ParentLeague's name (e.g. "Cleveland Autumn League" -> "autumn") so the S/A/V/W
    nav badges have something to show without requiring a manual admin step first.
    """
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("parent_leagues"):
        return
    existing = {c["name"] for c in inspector.get_columns("parent_leagues")}
    if "season_type" not in existing:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE parent_leagues ADD COLUMN season_type VARCHAR(20)"))

    for parent in ParentLeague.query.filter_by(season_type=None).all():
        lname = parent.name.lower()
        for season_type in (
            SEASON_TYPE_SUMMER,
            SEASON_TYPE_AUTUMN,
            SEASON_TYPE_VETERANS,
            SEASON_TYPE_WINTER,
        ):
            if season_type in lname:
                parent.season_type = season_type
                break
    db.session.commit()


def _migrate_season_type_parent_leagues(db):
    """Seed one ParentLeague per season-type badge (S/A/V/W) if missing.

    The four season types are templates everything else is defined from — a
    League can't exist without a ParentLeague — so all four should be
    available out of the box in every environment, not just Veterans/Winter
    (which have no organic creation path) while Summer/Autumn wait on an
    admin to create one by hand. Pre-populates one of each — using the season
    type's own colour from SEASON_TYPE_COLOURS as a sensible default the
    admin can still edit. Existing orphan League rows (from a database that
    predates the ParentLeague feature) are attached to the seeded Summer
    parent separately, by _migrate_league_format below.
    """
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("parent_leagues"):
        return
    # Every field spelled out explicitly (rather than relying on the ParentLeague
    # column defaults, which happen to match Summer's 1-set-to-8 but not Autumn's
    # real 2-sets-to-6) since Summer and Autumn share the same cleveland_autumn
    # format but need genuinely different rubber shapes.
    seeds = [
        dict(
            season_type=SEASON_TYPE_SUMMER,
            name="Cleveland Summer League",
            format=LEAGUE_FORMAT_CLEVELAND_AUTUMN,
            pairs_per_round=3,
            sets_per_rubber=1,
            games_per_set=8,
            tiebreak_at_games=8,
        ),
        dict(
            season_type=SEASON_TYPE_AUTUMN,
            name="Cleveland Autumn League",
            format=LEAGUE_FORMAT_CLEVELAND_AUTUMN,
            pairs_per_round=2,
            sets_per_rubber=2,
            games_per_set=6,
            tiebreak_at_games=6,
        ),
        dict(
            season_type=SEASON_TYPE_VETERANS,
            name="Cleveland Veterans League",
            format=LEAGUE_FORMAT_CLEVELAND_VETERANS,
            pairs_per_round=1,
            sets_per_rubber=1,
            games_per_set=8,
            tiebreak_at_games=8,
        ),
        dict(
            season_type=SEASON_TYPE_WINTER,
            name="North-East Winter League",
            format=LEAGUE_FORMAT_NE_WINTER,
            pairs_per_round=1,
            sets_per_rubber=1,
            games_per_set=8,
            tiebreak_at_games=8,
        ),
    ]
    for seed in seeds:
        if ParentLeague.query.filter_by(season_type=seed["season_type"]).first() is not None:
            continue
        db.session.add(ParentLeague(colour=SEASON_TYPE_COLOURS[seed["season_type"]], **seed))
    db.session.commit()


def _migrate_autumn_parent_league_config(db):
    """Correct an Autumn ParentLeague still stuck on an old broken seed.

    An earlier version of _migrate_season_type_parent_leagues seeded the
    Autumn ParentLeague with pairs_per_round=3 (Summer's value) and left
    sets_per_rubber/games_per_set/tiebreak_at_games at their column defaults
    (1/8/8, also Summer's real values) — Autumn actually needs 2 pairs and 2
    sets to 6 games. Only corrects a ParentLeague still at exactly those
    untouched seeded values, so an admin's deliberate customization is never
    overwritten. Also cascades the corrected pairs_per_round onto any League
    rows already created under it, since that normally only happens once, at
    League creation time — see "Once any league is created under a parent,
    that parent's format fields lock" in CLAUDE.md.
    """
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("parent_leagues"):
        return
    autumn = ParentLeague.query.filter_by(season_type=SEASON_TYPE_AUTUMN).first()
    if not autumn:
        return
    if not (
        autumn.pairs_per_round == 3
        and autumn.sets_per_rubber == 1
        and autumn.games_per_set == 8
        and autumn.tiebreak_at_games == 8
    ):
        return
    autumn.pairs_per_round = 2
    autumn.sets_per_rubber = 2
    autumn.games_per_set = 6
    autumn.tiebreak_at_games = 6
    for league in autumn.leagues:
        league.pairs_per_round = 2
    db.session.commit()


def _migrate_parent_league_default_colour(db):
    """Correct a ParentLeague still stuck on the raw column default colour.

    ParentLeague.colour defaults to "#2e7d32" (plain green) at the database
    level. Veterans/Winter/Autumn are always created by
    _migrate_season_type_parent_leagues, which sets colour explicitly, so
    they never hit this default — but a Summer ParentLeague that predates
    that seed (created by hand, back when the colour picker just inherited
    the column default and nobody thought to change it) can still be sitting
    on "#2e7d32" today, even after _migrate_parent_league_season_type
    backfills its season_type by name. Only corrects a row still at exactly
    that untouched default, so an admin's deliberate colour choice — even
    plain green — is never overwritten.
    """
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("parent_leagues"):
        return
    changed = False
    for parent in ParentLeague.query.filter_by(colour="#2e7d32").all():
        if parent.season_type in SEASON_TYPE_COLOURS:
            parent.colour = SEASON_TYPE_COLOURS[parent.season_type]
            changed = True
    if changed:
        db.session.commit()


def _migrate_league_format(db):
    """Add format column to leagues table if missing, and adopt orphan leagues.

    Must run after _migrate_season_type_parent_leagues, which guarantees a
    Summer ParentLeague already exists to adopt into.
    """
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("leagues"):
        return
    existing = {c["name"] for c in inspector.get_columns("leagues")}
    if "format" not in existing:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE leagues ADD COLUMN format VARCHAR(30) "
                    "NOT NULL DEFAULT 'cleveland_autumn'"
                )
            )

    orphans = League.query.filter_by(parent_league_id=None).all()
    if not orphans:
        return

    summer = ParentLeague.query.filter_by(season_type=SEASON_TYPE_SUMMER).first()
    for league in orphans:
        league.parent_league_id = summer.id
    db.session.commit()


def _migrate_fixture_round_label(db):
    """Add round_label column to fixtures table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("fixtures"):
        return
    existing = {c["name"] for c in inspector.get_columns("fixtures")}
    if "round_label" not in existing:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE fixtures ADD COLUMN round_label VARCHAR(30)"))


def _migrate_league_start_date(db):
    """Add start_date column to leagues table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("leagues"):
        return
    existing = {c["name"] for c in inspector.get_columns("leagues")}
    if "start_date" not in existing:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE leagues ADD COLUMN start_date DATE"))


def _migrate_league_round_start_dates(db):
    """Add round5_start_date and round9_start_date columns to leagues if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("leagues"):
        return
    existing = {c["name"] for c in inspector.get_columns("leagues")}
    with db.engine.begin() as conn:
        for col in ("round5_start_date", "round9_start_date"):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE leagues ADD COLUMN {col} DATE"))


def _migrate_league_catchup_weeks(db):
    """Backfill LeagueCatchupWeek rows from the legacy round5/round9_start_date columns."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("leagues") or not inspector.has_table("league_catchup_weeks"):
        return
    legacy_leagues = League.query.filter(
        (League.round5_start_date.isnot(None)) | (League.round9_start_date.isnot(None))
    )
    for league in legacy_leagues:
        if LeagueCatchupWeek.query.filter_by(league_id=league.id).first():
            continue
        for legacy_date in (league.round5_start_date, league.round9_start_date):
            if legacy_date:
                db.session.add(
                    LeagueCatchupWeek(
                        league_id=league.id, start_date=legacy_date - timedelta(days=7)
                    )
                )
    db.session.commit()


def _migrate_fixture_team_names(db):
    """Add home_team_name / away_team_name columns to fixtures if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("fixtures"):
        return
    existing = {c["name"] for c in inspector.get_columns("fixtures")}
    with db.engine.begin() as conn:
        for col in ("home_team_name", "away_team_name"):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE fixtures ADD COLUMN {col} VARCHAR(200)"))


def _migrate_away_team_nullable(db):
    """Make fixtures home_team_id & away_team_id nullable if existing schema has them NOT NULL."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("fixtures"):
        return
    cols = {c["name"]: c for c in inspector.get_columns("fixtures")}
    needs_migration = any(
        not cols.get(name, {}).get("nullable")
        for name in ("home_team_id", "away_team_id")
        if name in cols
    )
    if not needs_migration:
        return
    with db.engine.begin() as conn:
        conn.execute(text("""
                CREATE TABLE fixtures_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    date DATE NOT NULL,
                    league_id INTEGER REFERENCES leagues (id),
                    home_team_id INTEGER REFERENCES teams (id),
                    away_team_id INTEGER REFERENCES teams (id),
                    home_score INTEGER,
                    away_score INTEGER,
                    source_image VARCHAR(512),
                    created_at DATETIME
                )
                """))
        conn.execute(text("INSERT INTO fixtures_new SELECT * FROM fixtures"))
        conn.execute(text("DROP TABLE fixtures"))
        conn.execute(text("ALTER TABLE fixtures_new RENAME TO fixtures"))


def _migrate_squad_entry_team_id(db):
    """Add team_id column to fixture_squad_entries if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("fixture_squad_entries"):
        return
    existing = {c["name"] for c in inspector.get_columns("fixture_squad_entries")}
    if "team_id" not in existing:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE fixture_squad_entries "
                    "ADD COLUMN team_id INTEGER REFERENCES teams (id)"
                )
            )


def _migrate_team_captains(db):
    """Create team_captains table if missing (for databases predating this feature)."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("team_captains"):
        with db.engine.begin() as conn:
            conn.execute(text("""
                    CREATE TABLE team_captains (
                        id INTEGER NOT NULL PRIMARY KEY,
                        player_id INTEGER NOT NULL REFERENCES players (id),
                        team_id INTEGER NOT NULL REFERENCES teams (id),
                        season VARCHAR(20) NOT NULL DEFAULT '2026',
                        created_at DATETIME,
                        UNIQUE (team_id, season)
                    )
                    """))


def _migrate_team_match_fees_enabled(db):
    """Add match_fees_enabled column to teams table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("teams"):
        return
    existing = {c["name"] for c in inspector.get_columns("teams")}
    if "match_fees_enabled" not in existing:
        with db.engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE teams ADD COLUMN match_fees_enabled BOOLEAN NOT NULL DEFAULT 0")
            )


def _migrate_captain_users(db):
    """Create captain_users table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("captain_users"):
        with db.engine.begin() as conn:
            conn.execute(text("""
                    CREATE TABLE captain_users (
                        uid VARCHAR(128) NOT NULL PRIMARY KEY,
                        email VARCHAR(200) NOT NULL,
                        player_id INTEGER REFERENCES players (id),
                        sees_all_fixtures BOOLEAN NOT NULL DEFAULT 0,
                        created_at DATETIME
                    )
                    """))


def _migrate_captain_users_sees_all_fixtures(db):
    """Add sees_all_fixtures column to captain_users table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("captain_users"):
        return
    existing = {c["name"] for c in inspector.get_columns("captain_users")}
    if "sees_all_fixtures" not in existing:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE captain_users ADD COLUMN sees_all_fixtures "
                    "BOOLEAN NOT NULL DEFAULT 0"
                )
            )


def _cleanup_duplicate_captain_user_emails(db, firebase_configured):
    """Delete duplicate CaptainUser rows sharing an email, keeping only the live one.

    Nothing in the schema enforces one CaptainUser row per email, and a Firebase
    account deleted outside `admin_delete_user` (e.g. via the Firebase console)
    and later recreated with the same email leaves its old row behind — orphaned,
    but still matching that email. `pitchside_login`'s email lookup can't tell an
    orphan from the current row without checking Firebase, so it can pick a stale
    one: wrong `sees_all_fixtures`, or even a stale row with no `player_id` at
    all, which fails the login outright. `admin_add_user`/`admin_set_user_role`/
    `admin_edit_user_player` now also delete same-email rows before inserting a
    new one, so duplicates shouldn't recur going forward — this is the self-
    healing pass for whatever already exists (or slips through some other way,
    e.g. direct DB edits) and runs on every boot. For each email with more than
    one row: a live *plain* row (reflecting the account's real current
    captain/viewer role) always wins over a live synthetic `pitchside-admin-*`
    row, since a real row existing at all means that account isn't an admin any
    more (see `admin_set_user_role`); only when no plain row is live does a live
    synthetic row win. A no-op when Firebase isn't configured (no authority to
    check liveness against) or when no row in a group can be verified live
    (conservative — leaves ambiguous data alone rather than guessing).
    """
    if not firebase_configured:
        return
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("captain_users"):
        return
    prefix = "pitchside-admin-"
    by_email: dict = {}
    for row in CaptainUser.query.all():
        by_email.setdefault(row.email.lower(), []).append(row)

    def is_live(real_uid):
        try:
            return not firebase_auth.get_user(real_uid).disabled
        except Exception:
            return False

    changed = False
    for group in by_email.values():
        if len(group) <= 1:
            continue
        plain_rows = [r for r in group if not r.uid.startswith(prefix)]
        synthetic_rows = [r for r in group if r.uid.startswith(prefix)]
        keeper = next((r for r in plain_rows if is_live(r.uid)), None)
        if keeper is None:
            keeper = next((r for r in synthetic_rows if is_live(r.uid[len(prefix) :])), None)
        if keeper is None:
            continue
        for row in group:
            if row is not keeper:
                db.session.delete(row)
                changed = True
    if changed:
        db.session.commit()


def _migrate_login_events(db):
    """Create login_events table if missing; add logout_reason column if absent."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("login_events"):
        with db.engine.begin() as conn:
            conn.execute(text("""
                    CREATE TABLE login_events (
                        id INTEGER NOT NULL PRIMARY KEY,
                        uid VARCHAR(128) NOT NULL,
                        email VARCHAR(200) NOT NULL,
                        role VARCHAR(20) NOT NULL,
                        logged_in_at DATETIME NOT NULL,
                        logged_out_at DATETIME,
                        logout_reason VARCHAR(20)
                    )
                    """))
    else:
        cols = {c["name"] for c in inspector.get_columns("login_events")}
        if "logout_reason" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE login_events ADD COLUMN logout_reason VARCHAR(20)"))


def _migrate_fixture_walkover_winner(db):
    """Add walkover_winner column to fixtures table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("fixtures"):
        return
    existing = {c["name"] for c in inspector.get_columns("fixtures")}
    if "walkover_winner" not in existing:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE fixtures ADD COLUMN walkover_winner VARCHAR(10)"))


def _migrate_match_day_draft_submitted_by(db):
    """Add submitted_by_player_id column to match_day_drafts table if missing."""
    inspector = sa_inspect(db.engine)
    if not inspector.has_table("match_day_drafts"):
        return
    existing = {c["name"] for c in inspector.get_columns("match_day_drafts")}
    if "submitted_by_player_id" not in existing:
        with db.engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE match_day_drafts ADD COLUMN submitted_by_player_id INTEGER")
            )


LEAGUE_DISCIPLINE_CHOICES = [
    ("Men's Doubles", "Men's Doubles (2 men per pair)"),
    ("Women's Doubles", "Women's Doubles (2 women per pair)"),
    ("Mixed Doubles", "Mixed Doubles (1 man + 1 woman per pair)"),
]

# The organisation running each season type's leagues — Summer/Autumn/Veterans
# are the club's own "Cleveland" leagues; Winter is the external North-East
# regional league the club enters (see ParentLeague seed names in
# _migrate_season_type_parent_leagues). Kept as a fixed dict rather than
# read from ParentLeague.name, the same way SEASON_TYPE_COLOURS is kept fixed
# rather than read from ParentLeague.colour — an admin renaming a parent
# league's display name shouldn't change how new league names are built.
LEAGUE_NAME_ORG_PREFIX = {
    SEASON_TYPE_SUMMER: "Cleveland",
    SEASON_TYPE_AUTUMN: "Cleveland",
    SEASON_TYPE_VETERANS: "Cleveland",
    SEASON_TYPE_WINTER: "North-East",
}

# Admin-configurable via /admin/settings (AppSetting key "pitchside_window_days");
# these are the fallback default and the accepted range for that field.
PITCHSIDE_WINDOW_DAYS_DEFAULT = 10
PITCHSIDE_WINDOW_DAYS_MIN = 1
PITCHSIDE_WINDOW_DAYS_MAX = 60


def _derive_league_name(discipline, season_type, year):
    """Build a league name from its discipline, season type, and year.

    Matches the club's existing naming convention (e.g. "Cleveland Men's
    Doubles Autumn League 2026", "North-East Mixed Doubles Winter League
    2026"), previously typed out by hand into a static, year-locked dropdown
    — this replaces that with the same convention generated from three plain
    inputs, valid for any year.
    """
    season_label = SEASON_TYPE_LABELS.get(season_type, season_type)
    org_prefix = LEAGUE_NAME_ORG_PREFIX.get(season_type, "Cleveland")
    return f"{org_prefix} {discipline} {season_label} League {year}"


_LEAGUE_NAME_RE = re.compile(
    r"^(?:Cleveland|North-East) (Men's Doubles|Women's Doubles|Mixed Doubles) "
    r"(Summer|Autumn|Veterans|Winter) League (\d{4})$"
)


def _parse_league_name(name):
    """Best-effort reverse of `_derive_league_name`, for pre-filling the edit form.

    Returns (discipline, season_type, year) or (None, None, None) if `name`
    doesn't match the standard convention (e.g. a legacy or hand-typed name) —
    the edit form then falls back to blank selects the admin must fill in.
    """
    match = _LEAGUE_NAME_RE.match(name or "")
    if not match:
        return None, None, None
    discipline, season_label, year = match.groups()
    season_type = next(
        (st for st, label in SEASON_TYPE_LABELS.items() if label == season_label), None
    )
    return discipline, season_type, int(year)


def _short_league_name(name):
    """Shorten a league name to "Discipline Year" (e.g. "Men's Doubles 2026").

    Used on the Parent Leagues cards, where the org prefix and season label
    are already shown elsewhere on the card (parent name, season badge) —
    only the discipline and year add new information there. Falls back to
    the full name for legacy/hand-typed names `_parse_league_name` can't parse.
    """
    discipline, _season_type, year = _parse_league_name(name)
    if discipline is None:
        return name
    return f"{discipline} {year}"


def _touch_scores_updated():
    """Record the current UTC time as last_scores_updated in AppSetting."""
    from zoneinfo import ZoneInfo

    uk_tz = ZoneInfo("Europe/London")
    now_uk = datetime.now(tz=uk_tz)
    tz_label = "BST" if now_uk.dst() else "GMT"
    value = now_uk.strftime(f"%-d %b %Y %H:%M {tz_label}")
    setting = AppSetting.query.get("last_scores_updated")
    if setting is None:
        setting = AppSetting(key="last_scores_updated", value=value)
        db.session.add(setting)
    else:
        setting.value = value


def create_app(config=None):
    """Create and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=True)

    # Default configuration
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///tennis.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

    if config:
        app.config.update(config)

    db.init_app(app)
    register_commit_sync(app, db)

    with app.app_context():
        db.create_all()
        _migrate_away_team_nullable(db)
        _migrate_fixture_team_names(db)
        _migrate_league_start_date(db)
        _migrate_league_round_start_dates(db)
        # These two must run before any migration that ORM-queries League
        # (e.g. _migrate_league_format and _migrate_league_catchup_weeks
        # below) — the model declares `pairs_per_round` and
        # `parent_league_id`, so any such query would SELECT a column that
        # doesn't exist yet on an older database.
        _migrate_league_pairs_per_round(db)
        _migrate_league_parent_id(db)
        # Must run before any migration that ORM-queries League/ParentLeague
        # (e.g. _migrate_league_catchup_weeks below) — the model declares
        # `format`, so any such query would SELECT a column that doesn't exist
        # yet on an older database.
        _migrate_parent_league_format(db)
        _migrate_parent_league_season_type(db)
        _migrate_parent_league_default_colour(db)
        _migrate_season_type_parent_leagues(db)
        _migrate_autumn_parent_league_config(db)
        _migrate_league_format(db)
        _migrate_league_catchup_weeks(db)
        _migrate_fixture_round_label(db)
        _migrate_rubber_player_names(db)
        _migrate_rubber_tie_columns(db)
        _migrate_rubber_walkover_column(db)
        _migrate_squad_entry_team_id(db)
        _migrate_team_captains(db)
        _migrate_team_match_fees_enabled(db)
        _migrate_captain_users(db)
        _migrate_captain_users_sees_all_fixtures(db)
        _migrate_login_events(db)
        _migrate_fixture_walkover_winner(db)
        _migrate_match_day_draft_submitted_by(db)

    app.jinja_env.globals["fixture_round_label"] = fixture_round_label
    app.jinja_env.globals["autumn_away_rubber_number"] = autumn_away_rubber_number
    app.jinja_env.globals["league_format_label"] = lambda fmt: LEAGUE_FORMAT_LABELS.get(fmt, fmt)
    app.jinja_env.globals["league_format_choices"] = LEAGUE_FORMAT_CHOICES
    app.jinja_env.globals["season_type_choices"] = SEASON_TYPE_CHOICES
    app.jinja_env.globals["league_discipline_choices"] = LEAGUE_DISCIPLINE_CHOICES
    app.jinja_env.globals["league_name_org_prefix"] = LEAGUE_NAME_ORG_PREFIX
    app.jinja_env.globals["season_type_label"] = lambda st: SEASON_TYPE_LABELS.get(st, st)
    app.jinja_env.globals["season_type_colours"] = SEASON_TYPE_COLOURS
    app.jinja_env.globals["discipline_colours"] = DISCIPLINE_COLOURS
    app.jinja_env.globals["short_league_name"] = _short_league_name
    app.jinja_env.globals["league_format_description"] = league_format_description

    # ── Firebase Auth ────────────────────────────────────────────────────

    _firebase_project_id = os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get(
        "GOOGLE_CLOUD_PROJECT"
    )
    _firebase_api_key = (
        os.environ.get("FIREBASE_API_KEY")
        or get_secret(os.environ.get("FIREBASE_API_KEY_SECRET", "firebase-api-key"))
        or ""
    )
    _firebase_auth_domain = os.environ.get(
        "FIREBASE_AUTH_DOMAIN",
        f"{_firebase_project_id}.firebaseapp.com" if _firebase_project_id else "",
    )

    if _firebase_project_id:
        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app(options={"projectId": _firebase_project_id})

    with app.app_context():
        _cleanup_duplicate_captain_user_emails(db, bool(_firebase_project_id))

    _INACTIVITY_TIMEOUT = timedelta(minutes=10)
    # Holds the nonce, last-activity timestamp, and UID for the single permitted session.
    _active_session: dict = {"nonce": None, "last_activity": None, "uid": None}

    # Dev allowlist: when set, only these Firebase UIDs may access the app at all.
    _dev_allowed_uids: set = {
        uid.strip() for uid in os.environ.get("DEV_ALLOWED_UIDS", "").split(",") if uid.strip()
    }

    def _is_localhost_request():
        """Report whether this request is to a local dev server, never a deployed instance."""
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return False
        host = request.host.split(":")[0]
        return host in ("localhost", "127.0.0.1")

    @app.before_request
    def require_login():
        """Block write requests when unauthenticated; reads are always allowed.

        When DEV_ALLOWED_UIDS is set all requests (including GET) are blocked
        for unauthenticated users or users not in the allowlist.
        """
        if not _firebase_project_id:
            return
        if (
            request.path == "/login"
            or request.path.startswith("/auth/")
            or request.path.startswith("/static/")
            or request.path.startswith("/pitchside/")
        ):
            return
        if session.get("user_uid"):
            # Enforce single-session: invalidate if this session's nonce is stale.
            if session.get("session_nonce") != _active_session["nonce"]:
                session.clear()
            else:
                last = session.get("last_activity")
                now = datetime.now(timezone.utc)
                if last and (now - datetime.fromisoformat(last)) > _INACTIVITY_TIMEOUT:
                    _uid = session.get("user_uid")
                    if _uid:
                        _ev = (
                            LoginEvent.query.filter_by(uid=_uid, logged_out_at=None)
                            .order_by(LoginEvent.logged_in_at.desc())
                            .first()
                        )
                        if _ev:
                            _ev.logged_out_at = now.replace(tzinfo=None)
                            _ev.logout_reason = "timeout"
                            db.session.commit()
                    session.clear()
                    _active_session["nonce"] = None
                    _active_session["last_activity"] = None
                else:
                    session["last_activity"] = now.isoformat()
                    _active_session["last_activity"] = now

        if _dev_allowed_uids:
            uid = session.get("user_uid")
            if not uid:
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "Login required"}), 401
                return redirect(url_for("login", next=request.path))
            if uid not in _dev_allowed_uids:
                session.clear()
                _active_session["nonce"] = None
                _active_session["last_activity"] = None
                _active_session["uid"] = None
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "Access denied"}), 403
                return redirect(url_for("login"))
            return

        if not session.get("user_uid") and request.method != "GET":
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Login required"}), 401
            return redirect(url_for("login", next=request.path))

    @app.context_processor
    def inject_auth():
        """Inject auth state and role info into all templates."""
        logged_in = bool(session.get("user_uid"))
        role = session.get("user_role") if logged_in else None
        is_admin = logged_in and role == "admin"

        captain_team_ids = set()
        if logged_in and role == "captain":
            pid = session.get("captain_player_id")
            if pid:
                captain_team_ids = {
                    c.team_id
                    for c in TeamCaptain.query.filter_by(player_id=pid, season="2026").all()
                }

        badges = _season_badges()
        sel_type = _selected_season_type()
        active_badge = next((b for b in badges if b["type"] == sel_type), None)
        if active_badge:
            selected_season_label = active_badge["label"] + (
                f" {active_badge['year']}" if active_badge["year"] else ""
            )
        else:
            selected_season_label = "All Seasons"

        return {
            "logged_in": logged_in,
            "auth_enforced": bool(_firebase_project_id),
            "is_admin": is_admin,
            "user_role": role,
            "captain_team_ids": captain_team_ids,
            "maintenance_info": get_maintenance_info(),
            "selected_season_type": sel_type,
            "selected_season_label": selected_season_label,
            "season_badges": badges,
            "all_leagues": League.query.order_by(League.name).all(),
        }

    # ── Permission helpers ───────────────────────────────────────────────

    def _require_admin():
        """Return an error response if the current user is not an admin, else None."""
        if not session.get("user_uid"):
            return redirect(url_for("login", next=request.path))
        if session.get("user_role") != "admin":
            flash("This action requires admin access.", "error")
            return redirect(url_for("index"))
        return None

    def _get_captain_team_ids():
        """Return the set of team IDs managed by the currently logged-in captain."""
        pid = session.get("captain_player_id")
        if not pid:
            return set()
        return {c.team_id for c in TeamCaptain.query.filter_by(player_id=pid, season="2026").all()}

    def _contrast_text(hex_colour):
        """Return '#212529' or '#ffffff', whichever reads better on the given hex background."""
        h = (hex_colour or "#6c757d").lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        yiq = (r * 299 + g * 587 + b * 114) / 1000
        return "#212529" if yiq >= 150 else "#ffffff"

    def _season_badges():
        """Build the S/A/V/W season-switcher badges shown in the nav and on Home cards."""
        badges = []
        for value, label in SEASON_TYPE_CHOICES:
            group = current_season_group(value)
            colour = SEASON_TYPE_COLOURS[value]
            badges.append(
                {
                    "type": value,
                    "letter": SEASON_TYPE_BADGES[value],
                    "label": label,
                    "year": group["year"] if group else None,
                    "colour": colour,
                    "text_colour": _contrast_text(colour),
                    "available": bool(group),
                    "league_count": len(group["leagues"]) if group else 0,
                }
            )
        return badges

    def _selected_season_type():
        """Return the session's selected season type ('summer'/etc.), or None (All Seasons).

        Self-heals if the stored value is no longer a recognised season type.
        """
        season_type = session.get("selected_season_type")
        if season_type not in SEASON_TYPE_LABELS:
            session.pop("selected_season_type", None)
            return None
        return season_type

    def _selected_leagues():
        """Return the League rows for the session's selected season, or [] (All Seasons)."""
        return current_leagues_for_season_type(_selected_season_type())

    def _require_captain_for_team(team_id):
        """Return error response if the current user cannot edit this team, else None."""
        if session.get("user_role") == "admin":
            return None
        if team_id in _get_captain_team_ids():
            return None
        flash("You can only edit teams you captain.", "error")
        return redirect(url_for("team_detail", team_id=team_id))

    def _require_captain_for_fixture(fixture):
        """Return error response if current user cannot edit this fixture, else None."""
        if session.get("user_role") == "admin":
            return None
        my_teams = _get_captain_team_ids()
        if fixture.home_team_id in my_teams or fixture.away_team_id in my_teams:
            return None
        flash("You can only edit fixtures involving your team.", "error")
        return redirect(url_for("fixture_detail", fixture_id=fixture.id))

    @app.route("/login")
    def login():
        """Render the FirebaseUI sign-in page."""
        if session.get("user_uid"):
            return redirect(url_for("index"))
        return render_template(
            "login.html",
            firebase_api_key=_firebase_api_key,
            firebase_auth_domain=_firebase_auth_domain,
            firebase_project_id=_firebase_project_id,
            is_localhost=_is_localhost_request(),
            next=request.args.get("next", "/"),
        )

    @app.route("/auth/status")
    def auth_status():
        """Return whether the app currently has an active session (JSON, no auth required)."""
        if not _firebase_project_id:
            return jsonify({"locked": False})
        nonce = _active_session["nonce"]
        last = _active_session["last_activity"]
        if nonce is None or last is None:
            return jsonify({"locked": False})
        now = datetime.now(timezone.utc)
        elapsed = now - last
        if elapsed > _INACTIVITY_TIMEOUT:
            return jsonify({"locked": False})
        expires_in = int((_INACTIVITY_TIMEOUT - elapsed).total_seconds())
        return jsonify({"locked": True, "expires_in": expires_in})

    def _active_session_conflict():
        """Return a 409 response if another session is currently active, else None."""
        existing_nonce = _active_session["nonce"]
        existing_last = _active_session["last_activity"]
        if existing_nonce is not None and existing_last is not None:
            now = datetime.now(timezone.utc)
            remaining = _INACTIVITY_TIMEOUT - (now - existing_last)
            if remaining.total_seconds() > 0:
                mins = -(-int(remaining.total_seconds()) // 60)  # ceiling division
                return (
                    jsonify(
                        {
                            "error": "session_active",
                            "message": (
                                "Another user is currently logged in. Their session expires in"
                                f" approximately {mins} minute{'s' if mins != 1 else ''}."
                                " Please try again then."
                            ),
                        }
                    ),
                    409,
                )
        return None

    def _establish_session(uid, email, role, captain_player_id=None):
        """Set session/active-session state and log the login event."""
        import secrets as _secrets

        now = datetime.now(timezone.utc)
        nonce = _secrets.token_hex(16)
        _active_session["nonce"] = nonce
        _active_session["last_activity"] = now
        _active_session["uid"] = uid
        session.permanent = True
        session["user_uid"] = uid
        session["user_email"] = email
        session["session_nonce"] = nonce
        session["last_activity"] = now.isoformat()
        session["user_role"] = role
        if captain_player_id:
            session["captain_player_id"] = captain_player_id
        else:
            session.pop("captain_player_id", None)

        event = LoginEvent(uid=uid, email=email, role=role, logged_in_at=now.replace(tzinfo=None))
        db.session.add(event)
        db.session.commit()

        return jsonify({"ok": True, "role": role})

    @app.route("/auth/dev-login", methods=["POST"])
    def auth_dev_login():
        """Localhost-only shortcut: sign straight in as admin, bypassing Firebase."""
        if not _is_localhost_request():
            return jsonify({"error": "Not available"}), 403
        conflict = _active_session_conflict()
        if conflict:
            return conflict
        return _establish_session("local-dev-admin", "dev@localhost", "admin")

    @app.route("/auth/verify", methods=["POST"])
    def auth_verify():
        """Verify a Firebase ID token and establish a Flask session."""
        import requests as http

        token = (request.json or {}).get("token", "")
        if not token:
            return jsonify({"error": "No token"}), 400

        conflict = _active_session_conflict()
        if conflict:
            return conflict

        on_app_engine = bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))

        if on_app_engine:
            # Production: Admin SDK uses the App Engine default service account.
            try:
                decoded = firebase_auth.verify_id_token(token)
                uid = decoded["uid"]
                email = decoded.get("email", decoded.get("phone_number", uid))
            except Exception as e:
                app.logger.error("Token verification failed: %s", e)
                return jsonify({"error": "Invalid token"}), 401
        else:
            # Local dev: verify via Firebase Identity Toolkit REST API (no service account needed).
            _lookup_url = (
                f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={_firebase_api_key}"
            )
            resp = http.post(
                _lookup_url,
                json={"idToken": token},
                timeout=10,
            )
            if not resp.ok:
                app.logger.error("Token verification failed (REST): %s", resp.text)
                return jsonify({"error": "Invalid token"}), 401
            user = resp.json()["users"][0]
            uid = user["localId"]
            email = user.get("email", uid)

        if _dev_allowed_uids and uid not in _dev_allowed_uids:
            return jsonify({"error": "Access denied"}), 403

        # Determine role: no CaptainUser → admin; CaptainUser with a current TeamCaptain
        # assignment → captain; CaptainUser with no current assignment → viewer (read-only).
        captain_record = CaptainUser.query.get(uid)
        if captain_record:
            has_team = (
                TeamCaptain.query.filter_by(
                    player_id=captain_record.player_id, season="2026"
                ).first()
                is not None
            )
            role = "captain" if has_team else "viewer"
        else:
            role = "admin"

        return _establish_session(
            uid,
            email,
            role,
            captain_player_id=captain_record.player_id if captain_record else None,
        )

    @app.route("/auth/logout")
    def auth_logout():
        """Clear the session and redirect to login."""
        _uid = session.get("user_uid")
        if _uid:
            _ev = (
                LoginEvent.query.filter_by(uid=_uid, logged_out_at=None)
                .order_by(LoginEvent.logged_in_at.desc())
                .first()
            )
            if _ev:
                _ev.logged_out_at = datetime.now(timezone.utc).replace(tzinfo=None)
                _ev.logout_reason = "manual"
                db.session.commit()
        session.clear()
        _active_session["nonce"] = None
        _active_session["last_activity"] = None
        _active_session["uid"] = None
        return redirect(url_for("login"))

    # ── Login activity ───────────────────────────────────────────────────

    def _fmt_duration(seconds):
        """Format a duration in seconds as a human-readable string."""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        m, s = divmod(seconds, 60)
        if m < 60:
            return f"{m}m {s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h {m:02d}m"

    @app.route("/activity")
    def activity():
        """Show login activity for the last 30 days. Requires login."""
        if not session.get("user_uid"):
            return redirect(url_for("login", next="/activity"))
        enable_activity = AppSetting.query.filter_by(key="enable_activity_feature").first()
        is_enabled = enable_activity.value == "1" if enable_activity else False
        if not is_enabled and session.get("user_role") != "admin":
            flash("The Activity feature is disabled.", "error")
            return redirect(url_for("index"))
        thirty_days_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        events = (
            LoginEvent.query.filter(LoginEvent.logged_in_at >= thirty_days_ago)
            .order_by(LoginEvent.logged_in_at.desc())
            .all()
        )
        active_uid = _active_session.get("uid")
        enriched = []
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        for e in events:
            if e.logged_out_at:
                status = "signed_out" if e.logout_reason == "manual" else "timed_out"
                duration = _fmt_duration((e.logged_out_at - e.logged_in_at).total_seconds())
            elif e.uid == active_uid:
                status = "active"
                duration = _fmt_duration((now_utc - e.logged_in_at).total_seconds())
            else:
                status = "timed_out"
                duration = None
            enriched.append({"event": e, "status": status, "duration": duration})
        return render_template("activity.html", enriched=enriched)

    # ── Leagues ─────────────────────────────────────────────────────────

    @app.route("/leagues")
    def list_leagues():
        leagues = League.query.order_by(League.name).all()
        all_parents = ParentLeague.query.order_by(ParentLeague.name).all()
        league_groups = group_leagues_for_display(leagues)
        return render_template(
            "leagues.html",
            leagues=leagues,
            all_parents=all_parents,
            league_groups=league_groups,
        )

    @app.route("/leagues", methods=["POST"])
    def add_league():
        err = _require_admin()
        if err:
            return err
        season_type = request.form.get("season_type", "").strip()
        discipline = request.form.get("discipline", "").strip()
        year = request.form.get("year", type=int)
        description = request.form.get("description", "").strip()
        early_season_weeks = request.form.get("early_season_weeks", type=int) or 0
        team_commitment_threshold = request.form.get("team_commitment_threshold", type=int) or 0
        start_date_str = request.form.get("start_date", "").strip()
        catchup_dates_raw = request.form.getlist("catchup_week_dates")
        parent_league_id = request.form.get("parent_league_id", type=int)
        if season_type not in SEASON_TYPE_LABELS:
            flash("Season type is required.", "error")
            return redirect(url_for("list_leagues"))
        if season_type == SEASON_TYPE_WINTER:
            flash("Winter League — feature unavailable.", "error")
            return redirect(url_for("list_leagues"))
        if not discipline:
            flash("Discipline is required.", "error")
            return redirect(url_for("list_leagues"))
        if not year:
            flash("Year is required.", "error")
            return redirect(url_for("list_leagues"))
        name = _derive_league_name(discipline, season_type, year)
        if League.query.filter_by(name=name).first():
            flash(f"League '{name}' already exists.", "error")
            return redirect(url_for("list_leagues"))
        if not parent_league_id:
            flash("Parent league is required.", "error")
            return redirect(url_for("list_leagues"))
        parent = ParentLeague.query.get(parent_league_id)
        if not parent:
            flash("Selected parent league not found.", "error")
            return redirect(url_for("list_leagues"))
        if parent.season_type == SEASON_TYPE_WINTER:
            flash("Winter League — feature unavailable.", "error")
            return redirect(url_for("list_leagues"))
        start_date = None
        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid start date format.", "error")
                return redirect(url_for("list_leagues"))
        catchup_dates = []
        for raw in catchup_dates_raw:
            raw = raw.strip()
            if not raw:
                continue
            try:
                catchup_dates.append(datetime.strptime(raw, "%Y-%m-%d").date())
            except ValueError:
                flash("Invalid catch-up week date format.", "error")
                return redirect(url_for("list_leagues"))
        num_divisions = request.form.get("num_divisions", type=int) or 1
        league = League(
            name=name,
            description=description or None,
            start_date=start_date,
            parent_league_id=parent.id,
            pairs_per_round=parent.pairs_per_round,
            format=parent.format,
        )
        db.session.add(league)
        db.session.flush()
        for i in range(1, num_divisions + 1):
            db.session.add(Division(name=f"Division {i}", league_id=league.id))
        for catchup_date in catchup_dates:
            db.session.add(LeagueCatchupWeek(league_id=league.id, start_date=catchup_date))
        db.session.add(
            LeagueRestriction(
                league_id=league.id,
                early_season_weeks=early_season_weeks,
                team_commitment_threshold=team_commitment_threshold,
            )
        )
        db.session.commit()
        flash(f"League '{name}' created.", "success")
        return redirect(url_for("list_leagues"))

    @app.route("/leagues/<int:league_id>")
    def league_detail(league_id):
        league = League.query.get_or_404(league_id)
        divisions = league.divisions.order_by(Division.name).all()
        restriction = LeagueRestriction.query.filter_by(league_id=league.id).first()
        all_parents = ParentLeague.query.order_by(ParentLeague.name).all()
        edit_discipline, edit_season_type, edit_year = _parse_league_name(league.name)
        return render_template(
            "league_detail.html",
            league=league,
            divisions=divisions,
            restriction=restriction,
            all_parents=all_parents,
            edit_discipline=edit_discipline,
            edit_season_type=edit_season_type,
            edit_year=edit_year,
        )

    @app.route("/leagues/<int:league_id>/divisions", methods=["POST"])
    def add_division(league_id):
        err = _require_admin()
        if err:
            return err
        league = League.query.get_or_404(league_id)
        name = request.form.get("name", "").strip()
        if not name:
            flash("Division name is required.", "error")
            return redirect(url_for("league_detail", league_id=league_id))
        if Division.query.filter_by(name=name, league_id=league_id).first():
            flash(f"Division '{name}' already exists in {league.name}.", "error")
            return redirect(url_for("league_detail", league_id=league_id))
        division = Division(name=name, league_id=league_id)
        db.session.add(division)
        db.session.commit()
        flash(f"Division '{name}' added to {league.name}.", "success")
        return redirect(url_for("league_detail", league_id=league_id))

    @app.route("/leagues/<int:league_id>/restrictions", methods=["POST"])
    def update_restriction(league_id):
        err = _require_admin()
        if err:
            return err
        team_commitment = request.form.get("team_commitment_threshold", type=int)
        description = request.form.get("description", "").strip()
        restriction = LeagueRestriction.query.filter_by(league_id=league_id).first()

        if restriction:
            if team_commitment and team_commitment > 0:
                restriction.team_commitment_threshold = team_commitment
            restriction.description = description or (
                f"Team commitment after {team_commitment or 'disabled'} fixtures"
            )
            if not description and not team_commitment:
                db.session.delete(restriction)
        elif team_commitment and team_commitment > 0:
            restriction = LeagueRestriction(
                league_id=league_id,
                max_matches_per_team=0,
                team_commitment_threshold=team_commitment,
                description=description or (f"Team commitment after {team_commitment} fixtures"),
            )
            db.session.add(restriction)
        db.session.commit()
        flash("League restriction updated.", "success")
        return redirect(url_for("league_detail", league_id=league_id))

    @app.route("/leagues/<int:league_id>/edit", methods=["POST"])
    def edit_league(league_id):
        err = _require_admin()
        if err:
            return err
        league = League.query.get_or_404(league_id)
        season_type = request.form.get("season_type", "").strip()
        discipline = request.form.get("discipline", "").strip()
        year = request.form.get("year", type=int)
        description = request.form.get("description", "").strip()
        early_season_weeks = request.form.get("early_season_weeks", type=int) or 0
        team_commitment_threshold = request.form.get("team_commitment_threshold", type=int) or 0
        start_date_str = request.form.get("start_date", "").strip()
        catchup_dates_raw = request.form.getlist("catchup_week_dates")
        parent_league_id = request.form.get("parent_league_id", type=int)

        if season_type not in SEASON_TYPE_LABELS:
            flash("Season type is required.", "error")
            return redirect(url_for("league_detail", league_id=league_id))
        if season_type == SEASON_TYPE_WINTER:
            flash("Winter League — feature unavailable.", "error")
            return redirect(url_for("league_detail", league_id=league_id))
        if not discipline:
            flash("Discipline is required.", "error")
            return redirect(url_for("league_detail", league_id=league_id))
        if not year:
            flash("Year is required.", "error")
            return redirect(url_for("league_detail", league_id=league_id))
        name = _derive_league_name(discipline, season_type, year)

        if name != league.name and League.query.filter_by(name=name).first():
            flash(f"League '{name}' already exists.", "error")
            return redirect(url_for("league_detail", league_id=league_id))

        start_date = None
        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid start date format.", "error")
                return redirect(url_for("league_detail", league_id=league_id))

        catchup_dates = []
        for raw in catchup_dates_raw:
            raw = raw.strip()
            if not raw:
                continue
            try:
                catchup_dates.append(datetime.strptime(raw, "%Y-%m-%d").date())
            except ValueError:
                flash("Invalid catch-up week date format.", "error")
                return redirect(url_for("league_detail", league_id=league_id))

        league.name = name
        league.description = description or None
        league.start_date = start_date
        LeagueCatchupWeek.query.filter_by(league_id=league_id).delete()
        for catchup_date in catchup_dates:
            db.session.add(LeagueCatchupWeek(league_id=league_id, start_date=catchup_date))

        parent_locked = len(league.fixtures) > 0
        if parent_league_id and not parent_locked:
            parent = ParentLeague.query.get(parent_league_id)
            if not parent:
                flash("Selected parent league not found.", "error")
                return redirect(url_for("league_detail", league_id=league_id))
            if parent.season_type == SEASON_TYPE_WINTER:
                flash("Winter League — feature unavailable.", "error")
                return redirect(url_for("league_detail", league_id=league_id))
            league.parent_league_id = parent.id
            league.pairs_per_round = parent.pairs_per_round
            league.format = parent.format
        elif parent_league_id and parent_locked:
            flash(
                "Parent league cannot be changed: this league already has recorded fixtures.",
                "warning",
            )

        restriction = LeagueRestriction.query.filter_by(league_id=league_id).first()
        if restriction:
            restriction.early_season_weeks = early_season_weeks
            restriction.team_commitment_threshold = team_commitment_threshold
        else:
            db.session.add(
                LeagueRestriction(
                    league_id=league_id,
                    early_season_weeks=early_season_weeks,
                    team_commitment_threshold=team_commitment_threshold,
                )
            )

        db.session.commit()
        flash(f"League '{name}' updated.", "success")
        return redirect(url_for("league_detail", league_id=league_id))

    @app.route("/leagues/<int:league_id>/rename", methods=["POST"])
    def rename_league(league_id):
        err = _require_admin()
        if err:
            return err
        league = League.query.get_or_404(league_id)
        name = request.form.get("name", "").strip()
        if not name:
            flash("League name is required.", "error")
            return redirect(url_for("list_leagues"))
        if name != league.name and League.query.filter_by(name=name).first():
            flash(f"League '{name}' already exists.", "error")
            return redirect(url_for("list_leagues"))
        league.name = name
        db.session.commit()
        flash(f"League renamed to '{name}'.", "success")
        return redirect(url_for("list_leagues"))

    @app.route("/leagues/<int:league_id>/delete", methods=["POST"])
    def delete_league(league_id):
        err = _require_admin()
        if err:
            return err
        league = League.query.get_or_404(league_id)
        league_name = league.name
        db.session.delete(league)
        db.session.commit()
        flash(
            f"League '{league_name}' and all associated divisions, teams, and fixtures "
            "have been deleted.",
            "success",
        )
        return redirect(url_for("list_leagues"))

    # ── Parent Leagues ──────────────────────────────────────────────────

    @app.route("/parent-leagues")
    def list_parent_leagues():
        parents = ParentLeague.query.order_by(ParentLeague.name).all()
        return render_template("parent_leagues.html", parents=parents)

    @app.route("/parent-leagues/<int:parent_id>/edit", methods=["POST"])
    def edit_parent_league(parent_id):
        err = _require_admin()
        if err:
            return err
        parent = ParentLeague.query.get_or_404(parent_id)
        name = request.form.get("name", "").strip()
        colour = request.form.get("colour", "").strip() or parent.colour
        description = request.form.get("description", "").strip()
        if not name:
            flash("Parent league name is required.", "error")
            return redirect(url_for("list_parent_leagues"))
        if name != parent.name and ParentLeague.query.filter_by(name=name).first():
            flash(f"Parent league '{name}' already exists.", "error")
            return redirect(url_for("list_parent_leagues"))
        parent.name = name
        parent.colour = colour
        parent.description = description or None
        season_type = request.form.get("season_type", "").strip() or None
        parent.season_type = season_type if season_type in SEASON_TYPE_LABELS else None

        locked = len(parent.leagues) > 0
        if not locked:
            pairs_per_round = request.form.get("pairs_per_round", type=int)
            if pairs_per_round and pairs_per_round >= 1:
                parent.pairs_per_round = pairs_per_round
            parent.sets_per_rubber = request.form.get("sets_per_rubber", type=int) or 1
            parent.games_per_set = request.form.get("games_per_set", type=int) or 8
            parent.tiebreak_at_games = request.form.get("tiebreak_at_games", type=int) or 8
            format_ = request.form.get("format", "").strip()
            if format_ in LEAGUE_FORMAT_LABELS:
                parent.format = format_
        else:
            flash(
                f"Format fields for '{name}' are locked because leagues already use it; "
                "only name, colour, and description were updated.",
                "warning",
            )
        db.session.commit()
        flash(f"Parent league '{name}' updated.", "success")
        return redirect(url_for("list_parent_leagues"))

    @app.route("/parent-leagues/<int:parent_id>/delete", methods=["POST"])
    def delete_parent_league(parent_id):
        err = _require_admin()
        if err:
            return err
        parent = ParentLeague.query.get_or_404(parent_id)
        if len(parent.leagues) > 0:
            flash(
                f"Cannot delete '{parent.name}': {len(parent.leagues)} league(s) still use it. "
                "Reassign or delete those leagues first.",
                "error",
            )
            return redirect(url_for("list_parent_leagues"))
        db.session.delete(parent)
        db.session.commit()
        flash(f"Parent league '{parent.name}' deleted.", "success")
        return redirect(url_for("list_parent_leagues"))

    @app.route("/divisions/<int:division_id>/edit", methods=["POST"])
    def edit_division(division_id):
        err = _require_admin()
        if err:
            return err
        division = Division.query.get_or_404(division_id)
        league_id = division.league_id
        name = request.form.get("name", "").strip()

        if not name:
            flash("Division name is required.", "error")
            return redirect(url_for("league_detail", league_id=league_id))

        # Check if new name conflicts with another division in the same league
        if (
            name != division.name
            and Division.query.filter_by(name=name, league_id=league_id).first()
        ):
            flash(f"Division '{name}' already exists in this league.", "error")
            return redirect(url_for("league_detail", league_id=league_id))

        division.name = name
        db.session.commit()
        flash(f"Division '{name}' updated.", "success")
        return redirect(url_for("league_detail", league_id=league_id))

    @app.route("/divisions/<int:division_id>/teams", methods=["POST"])
    def add_team_to_division(division_id):
        """Create a new team directly within this division."""
        err = _require_admin()
        if err:
            return err
        division = Division.query.get_or_404(division_id)
        league_id = division.league_id
        name = request.form.get("name", "").strip()
        match_fees_enabled = request.form.get("match_fees_enabled") == "1"
        if not name:
            flash("Team name is required.", "error")
            return redirect(url_for("league_detail", league_id=league_id))
        team = Team(
            name=name,
            division_id=division.id,
            match_fees_enabled=match_fees_enabled,
        )
        db.session.add(team)
        db.session.commit()
        flash(f"Team '{name}' added to {division.name}.", "success")
        return redirect(url_for("league_detail", league_id=league_id))

    @app.route("/divisions/<int:division_id>/delete", methods=["POST"])
    def delete_division(division_id):
        err = _require_admin()
        if err:
            return err
        division = Division.query.get_or_404(division_id)
        league_id = division.league_id
        division_name = division.name
        db.session.delete(division)
        db.session.commit()
        flash(f"Division '{division_name}' and all associated teams have been deleted.", "success")
        return redirect(url_for("league_detail", league_id=league_id))

    # ── Routes ──────────────────────────────────────────────────────────

    @app.route("/help")
    def help_page():
        """Render the public user guide."""
        return render_template("help.html")

    @app.route("/help/captains")
    def captain_guide_page():
        """Render the captain-focused quick guide."""
        return render_template("captain_guide.html")

    @app.route("/select-season")
    def select_season():
        """Set or clear the session's selected season type, then redirect back."""
        season_type = request.args.get("season_type")
        if season_type in SEASON_TYPE_LABELS:
            session["selected_season_type"] = season_type
        else:
            session.pop("selected_season_type", None)
        next_url = request.args.get("next") or url_for("index")
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = url_for("index")
        return redirect(next_url)

    @app.route("/")
    def index():
        today = date.today()
        last_7_start = today - timedelta(days=7)
        next_7_end = today + timedelta(days=7)

        sel_leagues = _selected_leagues()
        sel_ids = [lg.id for lg in sel_leagues]

        this_week, next_week = [], []
        if sel_ids:
            base_fixtures = fixtures_query_for_league(sel_ids)
            this_week = (
                base_fixtures.filter(Fixture.date >= last_7_start, Fixture.date <= today)
                .order_by(Fixture.date.desc())
                .all()
            )
            next_week = (
                base_fixtures.filter(Fixture.date > today, Fixture.date <= next_7_end)
                .order_by(Fixture.date.asc())
                .all()
            )

        season_cards = []
        for badge in _season_badges():
            card_league_ids = [lg.id for lg in current_leagues_for_season_type(badge["type"])]
            card_fixtures_query = (
                fixtures_query_for_league(card_league_ids) if card_league_ids else None
            )
            season_cards.append(
                {
                    **badge,
                    "team_count": (
                        teams_query_for_league(card_league_ids).count() if card_league_ids else 0
                    ),
                    "fixture_count": (card_fixtures_query.count() if card_fixtures_query else 0),
                    "played_count": (
                        card_fixtures_query.filter(
                            db.or_(Fixture.home_score != 0, Fixture.away_score != 0)
                        ).count()
                        if card_fixtures_query
                        else 0
                    ),
                }
            )

        setting = AppSetting.query.get("last_scores_updated")
        last_updated = setting.value if setting else None

        return render_template(
            "index.html",
            this_week=this_week,
            next_week=next_week,
            today=today,
            season_cards=season_cards,
            deployed_at=last_updated,
        )

    # ── Teams ───────────────────────────────────────────────────────────

    @app.route("/teams")
    def list_teams():
        sel_leagues = _selected_leagues()
        sel_ids = [lg.id for lg in sel_leagues]
        teams = teams_query_for_league(sel_ids).order_by(Team.name).all()
        leagues = sel_leagues if sel_leagues else League.query.order_by(League.name).all()

        today = date.today()
        today_week_start = today - timedelta(days=(today.weekday() + 2) % 7)

        # Teams with a fixture today that has no score recorded yet (in progress).
        _scored_fixture_ids = {
            r[0]
            for r in db.session.query(Rubber.fixture_id)
            .filter(
                db.or_(
                    Rubber.home_set1.isnot(None),
                    Rubber.set1_tie.is_(True),
                    Rubber.set1_walkover.isnot(None),
                )
            )
            .distinct()
            .all()
        }
        live_team_ids = set()
        for _f in Fixture.query.filter(Fixture.date == today).all():
            if (
                not _f.home_score
                and not _f.away_score
                and not _f.walkover_winner
                and _f.id not in _scored_fixture_ids
            ):
                if _f.home_team_id:
                    live_team_ids.add(_f.home_team_id)
                if _f.away_team_id:
                    live_team_ids.add(_f.away_team_id)

        allocation_leagues = []
        for league in leagues:
            league_fixtures = (
                Fixture.query.filter_by(league_id=league.id).order_by(Fixture.date).all()
            )
            if not league_fixtures:
                continue

            # Club-wide eligible players for this league's squad dropdowns.
            # Filtered by interest, active membership, and league gender.
            _elig_q = Player.query.filter(
                Player.interest_team_play == "yes",
                Player.membership_status == "active",
            ).order_by(Player.last_name, Player.first_name)
            if "Women" in league.name:
                _elig_q = _elig_q.filter(Player.gender == "F")
            elif "Men" in league.name:
                _elig_q = _elig_q.filter(Player.gender == "M")
            league_club_eligible = _elig_q.all()

            _restriction = LeagueRestriction.query.filter_by(league_id=league.id).first()
            _early_n = _restriction.early_season_weeks if _restriction else 0
            _early_labels = {f"Round {r}" for r in range(1, _early_n + 1)}

            # Precompute eligibility annotations for allocation dropdowns.
            _commitments_by_player = {
                c.player_id: c
                for c in PlayerTeamCommitment.query.filter_by(league_id=league.id).all()
            }
            _commit_threshold = _restriction.team_commitment_threshold if _restriction else 0
            if _commit_threshold > 0:
                _team_id_expr = sa_case(
                    (PlayerMatchResult.side == "home", Fixture.home_team_id),
                    else_=Fixture.away_team_id,
                )
                _count_rows = (
                    db.session.query(
                        PlayerMatchResult.player_id,
                        _team_id_expr.label("team_id"),
                        sqlfunc.count(sqlfunc.distinct(Fixture.id)).label("cnt"),
                    )
                    .join(Rubber, PlayerMatchResult.rubber_id == Rubber.id)
                    .join(Fixture, Rubber.fixture_id == Fixture.id)
                    .filter(Fixture.league_id == league.id)
                    .group_by(PlayerMatchResult.player_id, _team_id_expr)
                    .all()
                )
                _fix_counts: dict = {(r.player_id, r.team_id): r.cnt for r in _count_rows}
            else:
                _fix_counts = {}

            # Index all teams appearing in this league's fixtures for O(1) lookups.
            _league_teams_by_id: dict = {}
            for _lf in league_fixtures:
                for _lt in (_lf.home_team, _lf.away_team):
                    if _lt and _lt.id not in _league_teams_by_id:
                        _league_teams_by_id[_lt.id] = _lt

            # Players who've crossed the *cumulative* threshold (fixtures for a team plus
            # every higher-ranked team) via actual results but have no formal record.
            _implicit_commit_by_player: dict = {}
            _cum_through: dict = {}  # (player_id, team_id) -> cumulative count through that team
            if _commit_threshold > 0:
                _league_teams_sorted = sorted(_league_teams_by_id.values(), key=_team_rank)
                _players_with_counts = {_pid for (_pid, _tid) in _fix_counts.keys()}
                for _pid in _players_with_counts:
                    _running = 0
                    _committed_tid = None
                    for _lt in _league_teams_sorted:
                        _running += _fix_counts.get((_pid, _lt.id), 0)
                        _cum_through[(_pid, _lt.id)] = _running
                        if _committed_tid is None and _running >= _commit_threshold:
                            _committed_tid = _lt.id
                    if _committed_tid is not None and _pid not in _commitments_by_player:
                        _implicit_commit_by_player[_pid] = _committed_tid

            def _player_meta_for_team(
                team,
                league_club_eligible=league_club_eligible,
                _commitments_by_player=_commitments_by_player,
                _implicit_commit_by_player=_implicit_commit_by_player,
                _league_teams_by_id=_league_teams_by_id,
                _commit_threshold=_commit_threshold,
                _cum_through=_cum_through,
            ):
                """Return {player_id: eligibility dict} for all league_club_eligible players."""
                if not team:
                    return {}
                result = {}
                for _p in league_club_eligible:
                    existing = _commitments_by_player.get(_p.id)
                    if existing:
                        if existing.team_id == team.id:
                            result[_p.id] = {
                                "would_commit": False,
                                "commitment_info": team.name,
                                "ineligible_reason": None,
                            }
                        else:
                            ct = existing.team
                            if ct:
                                if _team_rank(team) >= _team_rank(ct):
                                    ct_div = ct.division
                                    result[_p.id] = {
                                        "would_commit": False,
                                        "commitment_info": None,
                                        "ineligible_reason": (
                                            f"Tied to {ct.name}"
                                            + (f" ({ct_div.name})" if ct_div else "")
                                        ),
                                    }
                                else:
                                    result[_p.id] = {
                                        "would_commit": False,
                                        "commitment_info": None,
                                        "ineligible_reason": None,
                                    }
                            else:
                                result[_p.id] = {
                                    "would_commit": False,
                                    "commitment_info": None,
                                    "ineligible_reason": "Tied to another team",
                                }
                    elif _p.id in _implicit_commit_by_player:
                        # Player has hit the threshold via match results but no formal record yet.
                        imp_tid = _implicit_commit_by_player[_p.id]
                        imp_team = _league_teams_by_id.get(imp_tid)
                        if imp_tid == team.id:
                            result[_p.id] = {
                                "would_commit": False,
                                "commitment_info": team.name,
                                "ineligible_reason": None,
                            }
                        else:
                            if imp_team and _team_rank(team) >= _team_rank(imp_team):
                                ct_div = imp_team.division
                                result[_p.id] = {
                                    "would_commit": False,
                                    "commitment_info": None,
                                    "ineligible_reason": (
                                        f"Tied to {imp_team.name}"
                                        + (f" ({ct_div.name})" if ct_div else "")
                                    ),
                                }
                            else:
                                result[_p.id] = {
                                    "would_commit": False,
                                    "commitment_info": None,
                                    "ineligible_reason": None,
                                }
                    elif _commit_threshold > 0:
                        cur = _cum_through.get((_p.id, team.id), 0)
                        result[_p.id] = {
                            "would_commit": cur == _commit_threshold - 1,
                            "commitment_info": None,
                            "ineligible_reason": None,
                        }
                    else:
                        result[_p.id] = {
                            "would_commit": False,
                            "commitment_info": None,
                            "ineligible_reason": None,
                        }
                return result

            weeks_dict: dict = {}
            for fixture in league_fixtures:
                week_start = fixture.date - timedelta(days=(fixture.date.weekday() + 2) % 7)
                if week_start not in weeks_dict:
                    weeks_dict[week_start] = {"week_start": week_start, "fixtures": []}

                squad_entries = FixtureSquadEntry.query.filter_by(fixture_id=fixture.id).all()

                # Collect played player IDs by side in one pass over rubbers.
                home_played_ids: set = set()
                away_played_ids: set = set()
                for rubber in fixture.rubbers.all():
                    for pmr in rubber.player_results.all():
                        if pmr.side == "home":
                            home_played_ids.add(pmr.player_id)
                        else:
                            away_played_ids.add(pmr.player_id)
                played_ids = home_played_ids | away_played_ids

                def _make_entry(player):
                    """Lightweight squad-entry-like dict for template consumption."""
                    return {"player_id": player.id, "player": player}

                home_squad = sorted(
                    [e for e in squad_entries if e.team_id == fixture.home_team_id],
                    key=lambda e: (e.player.last_name, e.player.first_name),
                )
                away_squad = sorted(
                    [e for e in squad_entries if e.team_id == fixture.away_team_id],
                    key=lambda e: (e.player.last_name, e.player.first_name),
                )
                home_squad_ids = {e.player_id for e in home_squad}
                away_squad_ids = {e.player_id for e in away_squad}

                # Players who played but were never added to the squad list
                # (fixture entered directly without a squad reservation).
                for pid in sorted(home_played_ids - home_squad_ids):
                    p = Player.query.get(pid)
                    if p:
                        home_squad.append(_make_entry(p))
                        home_squad_ids.add(pid)
                for pid in sorted(away_played_ids - away_squad_ids):
                    p = Player.query.get(pid)
                    if p:
                        away_squad.append(_make_entry(p))
                        away_squad_ids.add(pid)

                home_eligible = (
                    [p for p in league_club_eligible if p.id not in home_squad_ids]
                    if fixture.home_team
                    else []
                )
                away_eligible = (
                    [p for p in league_club_eligible if p.id not in away_squad_ids]
                    if fixture.away_team
                    else []
                )

                weeks_dict[week_start]["fixtures"].append(
                    {
                        "fixture": fixture,
                        "home_squad": home_squad,
                        "away_squad": away_squad,
                        "played_ids": played_ids,
                        "home_eligible": home_eligible,
                        "away_eligible": away_eligible,
                        "home_player_meta": _player_meta_for_team(fixture.home_team),
                        "away_player_meta": _player_meta_for_team(fixture.away_team),
                    }
                )

            sorted_weeks = sorted(weeks_dict.values(), key=lambda w: w["week_start"])
            for week in sorted_weeks:
                week["is_current"] = week["week_start"] == today_week_start
                first_date = week["fixtures"][0]["fixture"].date
                week["round_label"] = fixture_round_label(
                    first_date,
                    league.start_date,
                    league.catchup_week_start_dates,
                )
                week["apply_conflict"] = week["round_label"] in _early_labels

            allocation_leagues.append({"league": league, "weeks": sorted_weeks})

        return render_template(
            "teams.html",
            teams=teams,
            leagues=leagues,
            allocation_leagues=allocation_leagues,
            today_week_start=today_week_start,
            today=today.strftime("%Y-%m-%d"),
            live_team_ids=live_team_ids,
        )

    @app.route("/teams/<int:team_id>/edit", methods=["POST"])
    def edit_team(team_id):
        err = _require_captain_for_team(team_id)
        if err:
            return err
        team = Team.query.get_or_404(team_id)
        name = request.form.get("name", "").strip()
        division_id = request.form.get("division_id", type=int)
        match_fees_enabled = request.form.get("match_fees_enabled") == "1"

        if not name:
            flash("Team name is required.", "error")
            return redirect(url_for("team_detail", team_id=team_id))

        team.name = name
        team.division_id = division_id if division_id else None
        team.match_fees_enabled = match_fees_enabled
        db.session.commit()
        flash(f"Team '{name}' updated.", "success")
        return redirect(url_for("team_detail", team_id=team_id))

    @app.route("/teams/<int:team_id>/captain", methods=["POST"])
    def set_team_captain(team_id):
        """Toggle a player as captain for this team this season."""
        err = _require_admin()
        if err:
            return err
        team = Team.query.get_or_404(team_id)
        player_id = request.form.get("player_id", type=int)
        if not player_id:
            flash("No player selected.", "error")
            return redirect(url_for("team_detail", team_id=team_id))
        player = Player.query.get_or_404(player_id)
        existing = TeamCaptain.query.filter_by(team_id=team_id, season="2026").first()
        if existing and existing.player_id == player_id:
            db.session.delete(existing)
            db.session.commit()
            flash(f"{player.name} is no longer captain of {team.name}.", "success")
        elif player.membership_status != "active":
            flash(f"{player.name} is inactive and can't be made captain.", "error")
        else:
            if existing:
                db.session.delete(existing)
                db.session.flush()
            db.session.add(TeamCaptain(player_id=player_id, team_id=team_id, season="2026"))
            db.session.commit()
            flash(f"{player.name} is now captain of {team.name}.", "success")
        return redirect(url_for("team_detail", team_id=team_id))

    @app.route("/teams/<int:team_id>/delete", methods=["POST"])
    def delete_team(team_id):
        """Delete a team along with its fixtures and every row that FKs to either.

        Several dependent tables (player_team_commitments, team_captains) have a
        NOT NULL team_id, and fixture_squad_entries/match_fees_paid/match_day_drafts
        have a NOT NULL fixture_id with no ORM-level cascade configured on Team or
        Fixture — deleting straight via db.session.delete(team) would otherwise have
        SQLAlchemy try to null those columns out and hit an IntegrityError. Rubbers
        and PlayerMatchResult rows are still handled by Fixture's own cascade.
        """
        err = _require_admin()
        if err:
            return err
        team = Team.query.get_or_404(team_id)
        team_name = team.name

        fixtures = Fixture.query.filter(
            db.or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id)
        ).all()
        fixture_ids = [f.id for f in fixtures]
        if fixture_ids:
            FixtureSquadEntry.query.filter(FixtureSquadEntry.fixture_id.in_(fixture_ids)).delete(
                synchronize_session=False
            )
            MatchFeePaid.query.filter(MatchFeePaid.fixture_id.in_(fixture_ids)).delete(
                synchronize_session=False
            )
            MatchDayDraft.query.filter(MatchDayDraft.fixture_id.in_(fixture_ids)).delete(
                synchronize_session=False
            )
        for fixture in fixtures:
            db.session.delete(fixture)

        PlayerTeamCommitment.query.filter_by(team_id=team_id).delete(synchronize_session=False)
        TeamCaptain.query.filter_by(team_id=team_id).delete(synchronize_session=False)
        FixtureSquadEntry.query.filter_by(team_id=team_id).delete(synchronize_session=False)
        MatchFeePaid.query.filter_by(team_id=team_id).delete(synchronize_session=False)

        db.session.delete(team)
        db.session.commit()
        flash(f"Team '{team_name}' and all associated fixtures have been deleted.", "success")
        return redirect(url_for("list_teams"))

    @app.route("/teams/<int:team_id>")
    def team_detail(team_id):
        team = Team.query.get_or_404(team_id)
        players = team.players.order_by(Player.last_name, Player.first_name).all()
        sel_ids = [lg.id for lg in _selected_leagues()]
        team_fixtures_q = fixtures_query_for_league(sel_ids).filter(
            db.or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id)
        )
        home_fixtures = (
            team_fixtures_q.filter(Fixture.home_team_id == team_id)
            .order_by(Fixture.date.asc())
            .all()
        )
        away_fixtures = (
            team_fixtures_q.filter(Fixture.away_team_id == team_id)
            .order_by(Fixture.date.asc())
            .all()
        )
        team_fixture_ids = {f.id for f in home_fixtures} | {f.id for f in away_fixtures}
        # Get eligibility info for each player if team is in a division/league
        eligibility = {}
        if team.division and team.division.league:
            league_id = team.division.league_id
            restriction = LeagueRestriction.query.filter_by(league_id=league_id).first()
            if restriction and restriction.max_matches_per_team > 0:
                for player in players:
                    eligibility[player.id] = check_player_eligibility(player.id, team.id, league_id)
        divisions = Division.query.order_by(Division.name).all()
        leagues = League.query.order_by(League.name).all()
        # Build the add-player dropdown: all gender-eligible players, existing members greyed out
        all_players = Player.query.order_by(Player.last_name, Player.first_name).all()

        league_name = team.division.league.name if team.division and team.division.league else ""
        if "Women" in league_name:
            gender_filter = "F"
        elif "Men" in league_name and "Women" not in league_name:
            gender_filter = "M"
        else:
            gender_filter = None

        team_player_ids = {p.id for p in players}
        available_players = [
            p
            for p in all_players
            if p.membership_status == "active"
            and (gender_filter is None or p.gender == gender_filter)
        ]
        captain = TeamCaptain.query.filter_by(team_id=team_id, season="2026").first()

        # Per-team stats for each player (single batched query)
        player_id_list = [p.id for p in players]
        team_results_rows = (
            PlayerMatchResult.query.join(Rubber, PlayerMatchResult.rubber_id == Rubber.id)
            .join(Fixture, Rubber.fixture_id == Fixture.id)
            .filter(
                PlayerMatchResult.player_id.in_(player_id_list),
                Fixture.id.in_(team_fixture_ids),
                db.or_(
                    db.and_(PlayerMatchResult.side == "home", Fixture.home_team_id == team_id),
                    db.and_(PlayerMatchResult.side == "away", Fixture.away_team_id == team_id),
                ),
            )
            .with_entities(
                PlayerMatchResult.player_id,
                PlayerMatchResult.won,
                Rubber.winner,
                Rubber.set1_walkover,
            )
            .all()
        )
        team_stats = {pid: {"wins": 0, "losses": 0} for pid in player_id_list}
        for pid, won, rubber_winner, walkover in team_results_rows:
            if walkover:
                continue
            if won:
                team_stats[pid]["wins"] += 1
            elif rubber_winner != "tie":
                team_stats[pid]["losses"] += 1

        return render_template(
            "team_detail.html",
            team=team,
            players=players,
            home_fixtures=home_fixtures,
            away_fixtures=away_fixtures,
            eligibility=eligibility,
            divisions=divisions,
            leagues=leagues,
            available_players=available_players,
            team_player_ids=team_player_ids,
            captain=captain,
            team_stats=team_stats,
        )

    # ── Players ─────────────────────────────────────────────────────────

    @app.route("/players/summary")
    def player_summary():
        """Display summary of all club players."""
        sel_ids = [lg.id for lg in _selected_leagues()]
        # Player list is club-wide and never season-scoped; only the Teams/Fixtures
        # column values below are filtered to the selected season.
        players = Player.query.order_by(Player.last_name, Player.first_name).all()

        scoped_fixture_ids = None
        if sel_ids:
            scoped_fixture_ids = {f.id for f in fixtures_query_for_league(sel_ids).all()}

        player_summary_data = []
        for player in players:
            player_info = {
                "id": player.id,
                "name": player.name,
                "gender": player.gender,
                "membership_status": player.membership_status,
                "interest_team_play": player.interest_team_play,
                "lta_number": player.lta_number,
                "teams": [],
                "total_teams": 0,
                "total_matches": 0,
                "commitments": [],
            }

            teams_dict = {}
            team_count = 0
            for team in player.teams:
                if team.division and team.division.league:
                    league = team.division.league
                    if sel_ids and league.id not in sel_ids:
                        continue
                    league_key = league.name
                    if league_key not in teams_dict:
                        teams_dict[league_key] = {"league": league.name, "teams": []}
                    teams_dict[league_key]["teams"].append(team.name)
                    team_count += 1

                    commitment = PlayerTeamCommitment.query.filter_by(
                        player_id=player.id, league_id=league.id, season="2026"
                    ).first()
                    if commitment:
                        player_info["commitments"].append(
                            {
                                "league": league.name,
                                "team": commitment.team.name,
                                "season": commitment.season,
                            }
                        )

            player_info["teams"] = list(teams_dict.values())
            player_info["total_teams"] = team_count
            matches_q = (
                db.session.query(Rubber.fixture_id)
                .join(PlayerMatchResult, PlayerMatchResult.rubber_id == Rubber.id)
                .filter(PlayerMatchResult.player_id == player.id)
            )
            if scoped_fixture_ids is not None:
                matches_q = matches_q.filter(Rubber.fixture_id.in_(scoped_fixture_ids))
            player_info["total_matches"] = matches_q.distinct().count()
            player_info["captaincies"] = [
                {"team_name": c.team.name}
                for c in TeamCaptain.query.filter_by(player_id=player.id, season="2026").all()
            ]
            player_summary_data.append(player_info)

        # Build ordered list of teams in leagues with eligibility restrictions (for grid).
        # Gendered leagues (M/F) come first; Mixed leagues are always appended last.
        # Within each league, ordered by division rank.
        gendered_teams = []
        mixed_teams = []
        gendered_groups = []
        mixed_groups = []
        restricted_teams_by_league = {}  # league_id -> [team_info, ...]
        _restrictions_q = LeagueRestriction.query.filter(
            LeagueRestriction.team_commitment_threshold > 0
        )
        if sel_ids:
            _restrictions_q = _restrictions_q.filter(LeagueRestriction.league_id.in_(sel_ids))
        for restriction in _restrictions_q.all():
            league = restriction.league
            threshold = restriction.team_commitment_threshold
            # Classify league gender using the same pattern used elsewhere in the app.
            if "Women" in league.name or "Ladies" in league.name:
                league_gender = "F"
            elif "Men" in league.name:
                league_gender = "M"
            else:
                league_gender = "mixed"

            divisions = sorted(league.divisions.all(), key=lambda d: _division_rank(d.name))
            league_team_infos = []
            for division in divisions:
                div_rank = _division_rank(division.name)
                for team in sorted(division.teams.all(), key=_team_rank):
                    info = {
                        "id": team.id,
                        "name": team.name,
                        "league_id": league.id,
                        "league_name": league.name,
                        "league_gender": league_gender,
                        "division_name": division.name,
                        "division_rank": div_rank,
                        "team_rank": _team_rank(team),
                        "threshold": threshold,
                    }
                    league_team_infos.append(info)
            if league_team_infos:
                group = {
                    "league_id": league.id,
                    "league_name": league.name,
                    "league_gender": league_gender,
                    "team_count": len(league_team_infos),
                }
                if league_gender == "mixed":
                    mixed_teams.extend(league_team_infos)
                    mixed_groups.append(group)
                else:
                    gendered_teams.extend(league_team_infos)
                    gendered_groups.append(group)
                restricted_teams_by_league[league.id] = league_team_infos

        restricted_teams = gendered_teams + mixed_teams
        league_groups = gendered_groups + mixed_groups

        # Compute eligibility grid using the same state logic as player_detail:
        # formal PlayerTeamCommitment first, then count-based fallback.
        # States: "available" | "eligible" | "committed" | "blocked"
        # The grid stays scoped to players rostered in the selected season (unlike the
        # main player list above, which is always club-wide).
        scoped_player_ids = (
            {p.id for p in players_query_for_league(sel_ids).all()} if sel_ids else None
        )
        elig_players = [
            p
            for p in player_summary_data
            if p["interest_team_play"] == "yes"
            and p["membership_status"] == "active"
            and (scoped_player_ids is None or p["id"] in scoped_player_ids)
        ]
        elig_player_ids = [p["id"] for p in elig_players]

        # Pre-fetch all commitments for interested players in one query.
        commitments_map = {}  # (player_id, league_id) -> PlayerTeamCommitment
        if elig_player_ids:
            for c in PlayerTeamCommitment.query.filter(
                PlayerTeamCommitment.player_id.in_(elig_player_ids)
            ).all():
                commitments_map[(c.player_id, c.league_id)] = c

        elig_grid = {p["id"]: {} for p in elig_players}

        for league_id, league_teams in restricted_teams_by_league.items():
            threshold = league_teams[0]["threshold"]

            for player_data in elig_players:
                player_id = player_data["id"]

                # Count fixtures played for each team in this league (side-matched).
                team_counts = {}
                for ti in league_teams:
                    team_counts[ti["id"]] = (
                        db.session.query(sqlfunc.count(sqlfunc.distinct(Fixture.id)))
                        .join(Rubber, Fixture.id == Rubber.fixture_id)
                        .join(PlayerMatchResult, Rubber.id == PlayerMatchResult.rubber_id)
                        .filter(
                            PlayerMatchResult.player_id == player_id,
                            Fixture.league_id == league_id,
                            db.or_(
                                db.and_(
                                    Fixture.home_team_id == ti["id"],
                                    PlayerMatchResult.side == "home",
                                ),
                                db.and_(
                                    Fixture.away_team_id == ti["id"],
                                    PlayerMatchResult.side == "away",
                                ),
                            ),
                        )
                        .scalar()
                        or 0
                    )

                # Determine the effective committed team: most senior team where the
                # player has either a formal commitment or has hit the *cumulative*
                # fixture count (this team plus every higher-ranked team) threshold.
                # league_teams is sorted by team_rank ascending (best team first).
                committed_team_id = None
                committed_rank = None
                committed_team_name = None

                # Cumulative count-based scan (best team first).
                running_total = 0
                for ti in league_teams:
                    running_total += team_counts.get(ti["id"], 0)
                    if running_total >= threshold:
                        committed_team_id = ti["id"]
                        committed_rank = ti["team_rank"]
                        committed_team_name = ti["name"]
                        break

                # Formal record: use if it's higher in hierarchy than the count-based result.
                existing = commitments_map.get((player_id, league_id))
                if existing:
                    ct = existing.team
                    formal_rank = _team_rank(ct) if ct else (float("inf"), float("inf"))
                    if committed_rank is None or formal_rank < committed_rank:
                        committed_team_id = existing.team_id
                        committed_rank = formal_rank
                        committed_team_name = ct.name if ct else None

                # Assign state per team, matching player_detail logic exactly.
                for ti in league_teams:
                    team_id = ti["id"]
                    count = team_counts.get(team_id, 0)
                    if committed_team_id is None:
                        state = "eligible"
                    elif team_id == committed_team_id:
                        state = "committed"
                    elif ti["team_rank"] >= committed_rank:
                        state = "blocked"
                    else:
                        state = "eligible"
                    elig_grid[player_id][team_id] = {
                        "state": state,
                        "count": count,
                        "threshold": threshold,
                        "committed_team_name": committed_team_name,
                    }

        return render_template(
            "player_summary.html",
            players=player_summary_data,
            total_players=len(player_summary_data),
            restricted_teams=restricted_teams,
            league_groups=league_groups,
            elig_players=elig_players,
            elig_grid=elig_grid,
        )

    @app.route("/players/admin/add-details", methods=["GET", "POST"])
    def admin_add_player_details():
        """Admin form to add club players with the editable/frozen field set."""
        err = _require_admin()
        if err:
            return err
        if request.method == "POST":
            first_name = (request.form.get("first_name") or "").strip()
            last_name = (request.form.get("last_name") or "").strip()
            gender = (request.form.get("gender") or "").strip().upper()
            membership_status = (request.form.get("membership_status") or "").strip().lower()
            interest_team_play = (request.form.get("interest_team_play") or "").strip().lower()
            lta_number = (request.form.get("lta_number") or "").strip()
            contact_telephone = (request.form.get("contact_telephone") or "").strip()
            confirm_duplicate = request.form.get("confirm_duplicate") == "1"

            def _invalid(msg):
                flash(msg, "error")
                return redirect(url_for("admin_add_player_details"))

            if not first_name:
                return _invalid("First name is required.")
            if not last_name:
                return _invalid("Last name is required.")
            if gender not in {"M", "F"}:
                return _invalid("Gender must be 'M' or 'F'.")
            if membership_status not in {"active", "inactive"}:
                return _invalid("Membership status must be 'active' or 'inactive'.")
            if interest_team_play not in {"yes", "no"}:
                return _invalid("Interest in team play must be 'yes' or 'no'.")

            if not confirm_duplicate:
                # Exact full-name match (case-insensitive) or same last name + gender.
                fn_lower = first_name.lower()
                ln_lower = last_name.lower()
                similar = Player.query.filter(
                    db.or_(
                        db.and_(
                            db.func.lower(Player.first_name) == fn_lower,
                            db.func.lower(Player.last_name) == ln_lower,
                        ),
                        db.and_(
                            db.func.lower(Player.last_name) == ln_lower,
                            Player.gender == gender,
                        ),
                    )
                ).all()
                if similar:
                    return render_template(
                        "add_player.html",
                        similar_players=similar,
                        form_data={
                            "first_name": first_name,
                            "last_name": last_name,
                            "gender": gender,
                            "membership_status": membership_status,
                            "interest_team_play": interest_team_play,
                            "lta_number": lta_number,
                            "contact_telephone": contact_telephone,
                        },
                    )

            player = Player(
                first_name=first_name,
                last_name=last_name,
                gender=gender,
                membership_status=membership_status,
                interest_team_play=interest_team_play,
                lta_number=lta_number or None,
                contact_telephone=contact_telephone or None,
                miscellaneous=None,
            )
            db.session.add(player)
            db.session.commit()

            flash(f"Player '{player.name}' added.", "success")
            return redirect(url_for("player_detail", player_id=player.id))

        return render_template("add_player.html")

    # Legacy route: add existing player to a team or create a minimal player by name.
    # (Kept to avoid breaking existing UI flows on team pages.)
    @app.route("/players", methods=["POST"])
    def add_player():
        err = _require_admin()
        if err:
            return err
        team_id = request.form.get("team_id", type=int)

        player_ids = request.form.getlist("player_id")
        name = request.form.get("name", "").strip()

        if not team_id:
            flash("Team is required.", "error")
            return redirect(url_for("list_teams"))

        team = Team.query.get_or_404(team_id)

        # Case 1: Add one or more existing players from multi-select
        if player_ids:
            added, skipped, inactive = [], [], []
            for pid in player_ids:
                player = Player.query.get(int(pid))
                if player is None:
                    continue
                if team in player.teams:
                    skipped.append(player.name)
                elif player.membership_status != "active":
                    inactive.append(player.name)
                else:
                    player.teams.append(team)
                    added.append(player.name)
            db.session.commit()
            if added:
                flash(f"Added to {team.name}: {', '.join(added)}.", "success")
            if skipped:
                flash(f"Already in {team.name}: {', '.join(skipped)}.", "error")
            if inactive:
                flash(f"Inactive, not added: {', '.join(inactive)}.", "error")
        # Case 2: Create new player with name
        elif name:
            player = _find_player_by_name(name)
            if not player:
                first, _, last = name.rpartition(" ")
                if not first:
                    first, last = last, ""
                player = Player(
                    first_name=first,
                    last_name=last,
                    gender="U",
                    membership_status="inactive",
                    interest_team_play="no",
                )
                db.session.add(player)
                db.session.flush()
            if team in player.teams:
                flash(f"Player '{name}' is already in {team.name}.", "error")
            else:
                player.teams.append(team)
                db.session.commit()
                flash(f"Player '{name}' added to {team.name}.", "success")
        else:
            flash("Please select a player or enter a player name.", "error")

        return redirect(url_for("team_detail", team_id=team_id))

    @app.route("/players/<int:player_id>")
    def player_detail(player_id):
        player = Player.query.get_or_404(player_id)
        sel_leagues = _selected_leagues()
        sel_ids = [lg.id for lg in sel_leagues]
        all_results = (
            PlayerMatchResult.query.filter_by(player_id=player.id)
            .join(Rubber)
            .join(Fixture)
            .order_by(Fixture.date.desc(), Rubber.rubber_number)
            .all()
        )
        total_rubbers = len(all_results)

        # Match History is scoped to the active season badge; under "All Seasons"
        # every fixture is shown (with a per-row season badge to tell them apart).
        if sel_ids:
            scoped_fixture_ids = {f.id for f in fixtures_query_for_league(sel_ids).all()}
            season_results = [r for r in all_results if r.rubber.fixture_id in scoped_fixture_ids]
        else:
            season_results = all_results

        # Summary stats are season-scoped and only shown when a season is selected —
        # never a career aggregate.
        season_wins = sum(1 for r in season_results if r.won and not r.rubber.set1_walkover)
        season_losses = sum(
            1
            for r in season_results
            if not r.won and r.rubber.winner != "tie" and not r.rubber.set1_walkover
        )

        def _fixture_season_badge(fixture):
            league = _effective_league(fixture)
            season_type = (
                league.parent_league.season_type if league and league.parent_league else None
            )
            if not season_type:
                return None
            colour = SEASON_TYPE_COLOURS[season_type]
            year = _league_year(league)
            return {
                "letter": SEASON_TYPE_BADGES[season_type],
                "label": SEASON_TYPE_LABELS[season_type] + (f" {year}" if year else ""),
                "colour": colour,
                "text_colour": _contrast_text(colour),
            }

        # Group rubbers into one row per fixture
        fixture_rows = []
        seen = {}
        for result in season_results:
            fixture = result.rubber.fixture
            if fixture.id not in seen:
                if result.side == "home":
                    opposition = (
                        fixture.away_team.name
                        if fixture.away_team
                        else fixture.away_team_name or "—"
                    )
                else:
                    opposition = (
                        fixture.home_team.name
                        if fixture.home_team
                        else fixture.home_team_name or "—"
                    )
                player_team = fixture.home_team if result.side == "home" else fixture.away_team
                row = {
                    "fixture": fixture,
                    "opposition": opposition,
                    "player_team": player_team,
                    "rubber_outcomes": [],
                    "season_badge": _fixture_season_badge(fixture),
                }
                seen[fixture.id] = row
                fixture_rows.append(row)
            outcome = (
                "walkover"
                if result.rubber.set1_walkover
                else "tied" if result.rubber.winner == "tie" else "won" if result.won else "lost"
            )
            seen[fixture.id]["rubber_outcomes"].append(outcome)

        # Attach fee-paid status to each row
        if fixture_rows:
            paid_fixture_ids = {
                r.fixture_id
                for r in MatchFeePaid.query.filter(
                    MatchFeePaid.player_id == player.id,
                    MatchFeePaid.fixture_id.in_([r["fixture"].id for r in fixture_rows]),
                ).all()
            }
            for row in fixture_rows:
                row["fee_paid"] = row["fixture"].id in paid_fixture_ids

        # Get eligibility info per league (deduplicated), scoped to the active season —
        # with no season selected there's no single active league to show this against.
        eligibility_info = []
        seen_league_ids = set()
        for team in player.teams if sel_ids else []:
            if team.division and team.division.league:
                league = team.division.league
                if league.id in seen_league_ids or league.id not in sel_ids:
                    continue
                seen_league_ids.add(league.id)

                info = check_player_eligibility(player.id, team.id, league.id)
                info["league"] = league

                # Total fixtures played in this league across all teams
                info["league_fixtures"] = (
                    db.session.query(sqlfunc.count(sqlfunc.distinct(Fixture.id)))
                    .join(Rubber, Fixture.id == Rubber.fixture_id)
                    .join(PlayerMatchResult, Rubber.id == PlayerMatchResult.rubber_id)
                    .filter(
                        PlayerMatchResult.player_id == player.id,
                        Fixture.league_id == league.id,
                    )
                    .scalar()
                    or 0
                )

                league_teams = (
                    Team.query.join(Division)
                    .filter(Division.league_id == league.id)
                    .order_by(Team.name)
                    .all()
                )
                team_matrix = []
                for lt in league_teams:
                    count = (
                        db.session.query(sqlfunc.count(sqlfunc.distinct(Fixture.id)))
                        .join(Rubber, Fixture.id == Rubber.fixture_id)
                        .join(PlayerMatchResult, Rubber.id == PlayerMatchResult.rubber_id)
                        .filter(
                            PlayerMatchResult.player_id == player.id,
                            Fixture.league_id == league.id,
                            db.or_(
                                db.and_(
                                    Fixture.home_team_id == lt.id,
                                    PlayerMatchResult.side == "home",
                                ),
                                db.and_(
                                    Fixture.away_team_id == lt.id,
                                    PlayerMatchResult.side == "away",
                                ),
                            ),
                        )
                        .scalar()
                        or 0
                    )
                    m = re.search(r"\b([A-Z])\b", lt.name)
                    label = m.group(1) if m else lt.name.split()[-1]
                    team_matrix.append(
                        {"team": lt, "count": count, "label": label, "team_rank": _team_rank(lt)}
                    )

                # Determine committed team for this league: the more senior of the formal
                # record and the team where *cumulative* fixtures (this team plus every
                # higher-ranked team) reach the threshold. team_matrix is ordered by
                # Team.name; sort by team_rank for the scan.
                committed_team_id = None
                committed_rank = None
                committed_team = None
                existing_commitment = PlayerTeamCommitment.query.filter_by(
                    player_id=player.id, league_id=league.id
                ).first()
                if existing_commitment:
                    committed_team = existing_commitment.team
                    committed_team_id = existing_commitment.team_id
                    ct = existing_commitment.team
                    committed_rank = _team_rank(ct) if ct else (float("inf"), float("inf"))
                if info["commitment_threshold"]:
                    running_total = 0
                    for entry in sorted(team_matrix, key=lambda e: e["team_rank"]):
                        running_total += entry["count"]
                        if running_total >= info["commitment_threshold"]:
                            if committed_rank is None or entry["team_rank"] < committed_rank:
                                committed_team = entry["team"]
                                committed_team_id = entry["team"].id
                                committed_rank = entry["team_rank"]
                            break
                info["committed_team"] = committed_team

                for entry in team_matrix:
                    if committed_team_id is None:
                        entry["state"] = "available"
                    elif entry["team"].id == committed_team_id:
                        entry["state"] = "committed"
                    elif entry["team_rank"] >= committed_rank:
                        entry["state"] = "blocked"
                    else:
                        entry["state"] = "eligible"

                info["team_matrix"] = team_matrix

                restriction = LeagueRestriction.query.filter_by(league_id=league.id).first()
                num_early_rounds = restriction.early_season_weeks if restriction else 0
                early_round_labels = [f"Round {r}" for r in range(1, num_early_rounds + 1)]
                info["num_early_rounds"] = num_early_rounds
                team_id_to_name = {lt.id: lt.name for lt in league_teams}
                player_team_ids = {t.id for t in player.teams}

                played_rows = (
                    db.session.query(
                        Fixture.round_label,
                        Fixture.date,
                        Fixture.home_team_id,
                        Fixture.away_team_id,
                        PlayerMatchResult.side,
                    )
                    .join(Rubber, Fixture.id == Rubber.fixture_id)
                    .join(PlayerMatchResult, Rubber.id == PlayerMatchResult.rubber_id)
                    .filter(
                        PlayerMatchResult.player_id == player.id,
                        Fixture.league_id == league.id,
                    )
                    .distinct()
                    .all()
                )
                early_rounds_played = {}
                for stored_label, fdate, home_id, away_id, side in played_rows:
                    label = stored_label or fixture_round_label(
                        fdate,
                        league.start_date,
                        league.catchup_week_start_dates,
                    )
                    if label in early_round_labels:
                        rnum = int(label.split()[-1])
                        tid = home_id if side == "home" else away_id
                        early_rounds_played[rnum] = team_id_to_name.get(tid, "")
                info["early_rounds_played"] = early_rounds_played

                squad_rows = (
                    db.session.query(
                        Fixture.round_label,
                        Fixture.date,
                        Fixture.home_team_id,
                        Fixture.away_team_id,
                    )
                    .join(FixtureSquadEntry, Fixture.id == FixtureSquadEntry.fixture_id)
                    .filter(
                        FixtureSquadEntry.player_id == player.id,
                        Fixture.league_id == league.id,
                    )
                    .distinct()
                    .all()
                )
                early_rounds_squad = {}
                for stored_label, fdate, home_id, away_id in squad_rows:
                    label = stored_label or fixture_round_label(
                        fdate,
                        league.start_date,
                        league.catchup_week_start_dates,
                    )
                    if label in early_round_labels:
                        rnum = int(label.split()[-1])
                        if rnum in early_rounds_played:
                            continue
                        tid = home_id if home_id in player_team_ids else away_id
                        early_rounds_squad[rnum] = team_id_to_name.get(tid, "")
                info["early_rounds_squad"] = early_rounds_squad

                eligibility_info.append(info)
        user_role = session.get("user_role") if session.get("user_uid") else None
        if user_role == "admin":
            show_phone = True
        elif user_role == "captain":
            captain_teams = _get_captain_team_ids()
            player_team_ids = {t.id for t in player.teams}
            own_profile = session.get("captain_player_id") == player.id
            show_phone = own_profile or bool(captain_teams & player_team_ids)
        else:
            show_phone = False

        season_teams = [t for t in player.teams if t.division and t.division.league_id in sel_ids]

        return render_template(
            "player_detail.html",
            player=player,
            fixture_rows=fixture_rows,
            season_wins=season_wins,
            season_losses=season_losses,
            total_rubbers=total_rubbers,
            eligibility_info=eligibility_info,
            season_teams=season_teams,
            show_phone=show_phone,
        )

    @app.route("/players/<int:player_id>/teams/<int:team_id>/remove", methods=["POST"])
    def remove_player_from_team(player_id, team_id):
        err = _require_admin()
        if err:
            return err
        player = Player.query.get_or_404(player_id)
        team = Team.query.get_or_404(team_id)

        if team in player.teams:
            player.teams.remove(team)
            db.session.commit()
            flash(f"Player '{player.name}' removed from {team.name}.", "success")
        else:
            flash(f"Player '{player.name}' is not in {team.name}.", "error")

        return redirect(url_for("team_detail", team_id=team_id))

    @app.route("/players/<int:player_id>/edit", methods=["GET", "POST"])
    def edit_player(player_id):
        err = _require_admin()
        if err:
            return err
        player = Player.query.get_or_404(player_id)
        if request.method == "POST":
            first_name = (request.form.get("first_name") or "").strip()
            last_name = (request.form.get("last_name") or "").strip()
            gender = (request.form.get("gender") or "").strip().upper()
            membership_status = (request.form.get("membership_status") or "").strip().lower()
            interest_team_play = (request.form.get("interest_team_play") or "").strip().lower()
            lta_number = (request.form.get("lta_number") or "").strip()
            contact_telephone = (request.form.get("contact_telephone") or "").strip()

            def _invalid(msg):
                flash(msg, "error")
                return redirect(url_for("edit_player", player_id=player_id))

            if not first_name:
                return _invalid("First name is required.")
            if not last_name:
                return _invalid("Last name is required.")
            if gender not in {"M", "F"}:
                return _invalid("Gender must be 'M' or 'F'.")
            if membership_status not in {"active", "inactive"}:
                return _invalid("Membership status must be 'active' or 'inactive'.")
            if interest_team_play not in {"yes", "no"}:
                return _invalid("Interest in team play must be 'yes' or 'no'.")
            player.first_name = first_name
            player.last_name = last_name
            player.gender = gender
            player.membership_status = membership_status
            player.interest_team_play = interest_team_play
            player.lta_number = lta_number or None
            player.contact_telephone = contact_telephone or None
            db.session.commit()
            flash(f"Player '{player.name}' updated.", "success")
            return redirect(url_for("player_summary"))

        return render_template("edit_player.html", player=player)

    @app.route("/players/<int:player_id>/deactivate", methods=["POST"])
    def deactivate_player(player_id):
        """Mark a player inactive instead of deleting them.

        Their fixture history and team roster references are left untouched;
        they simply stop being selectable for new activity and their name
        fades throughout the app until an admin reactivates them via Edit.
        """
        err = _require_admin()
        if err:
            return err
        player = Player.query.get_or_404(player_id)
        player.membership_status = "inactive"
        db.session.commit()
        flash(f"Player '{player.name}' has been deactivated.", "success")
        return redirect(url_for("player_summary"))

    # ── Fixtures ────────────────────────────────────────────────────────

    def _parse_ics(content, our_team_name=""):
        """Parse ICS bytes into a list of event dicts.

        Each dict has: date, summary, home_str, away_str, our_side.
        Summaries are expected in the form 'Home Team - Away Team'.
        our_team_name is matched (case-insensitive substring) against the
        home/away parts to determine which side the tagged team is on.
        """
        ics_text = content.decode("utf-8", errors="replace")
        lines = ics_text.splitlines()
        unfolded = []
        for line in lines:
            if line.startswith((" ", "\t")) and unfolded:
                unfolded[-1] += line[1:]
            else:
                unfolded.append(line)

        events = []
        current = {}
        in_event = False
        for line in unfolded:
            stripped = line.strip()
            if stripped == "BEGIN:VEVENT":
                in_event = True
                current = {}
            elif stripped == "END:VEVENT":
                if "date" in current and "summary" in current:
                    events.append(current)
                in_event = False
            elif in_event and ":" in stripped:
                key, _, value = stripped.partition(":")
                key_base = key.split(";")[0].upper()
                if key_base == "DTSTART":
                    date_str = value.replace("Z", "")
                    try:
                        if "T" in date_str:
                            dt = datetime.strptime(date_str[:15], "%Y%m%dT%H%M%S")
                        else:
                            dt = datetime.strptime(date_str[:8], "%Y%m%d")
                        current["date"] = dt.date()
                    except ValueError:
                        pass
                elif key_base == "SUMMARY":
                    current["summary"] = value

        def _side_matches(team_lower, side_str):
            # Normalise: strip parens/apostrophes, expand possessives, collapse whitespace.
            def _norm(s):
                s = re.sub(r"[()']", "", s.lower())
                s = re.sub(r"\bmen's\b", "men", s)
                s = re.sub(r"\bwomen's\b", "women", s)
                return " ".join(s.split())

            side_norm = _norm(side_str)
            team_norm = _norm(team_lower)

            # Substring match (original logic)
            if team_norm in side_norm or side_norm in team_norm:
                return True

            # Token-set equality: handles reordered words e.g. "Yarm B Men" vs "Yarm Men B"
            return set(team_norm.split()) == set(side_norm.split())

        team_lower = our_team_name.lower()
        for event in events:
            parts = event["summary"].split(" - ", 1)
            if len(parts) == 2:
                event["home_str"] = parts[0].strip()
                event["away_str"] = parts[1].strip()
                if team_lower and _side_matches(team_lower, event["home_str"]):
                    event["our_side"] = "home"
                elif team_lower and _side_matches(team_lower, event["away_str"]):
                    event["our_side"] = "away"
                else:
                    event["our_side"] = "home"
            else:
                event["home_str"] = event["summary"]
                event["away_str"] = ""
                event["our_side"] = "home"

        return sorted(events, key=lambda e: e["date"])

    @app.route("/fixtures/import-ics", methods=["GET", "POST"])
    def import_ics():
        """Import fixtures from a Google Calendar .ics file."""
        err = _require_admin()
        if err:
            return err
        # Scope to the currently selected season badge (e.g. Autumn), same as
        # /teams and /fixtures, so the "Our Team" list only offers this
        # season's teams instead of every season's identically-named ones.
        # "All Seasons" (no badge selected) falls back to everything.
        sel_leagues = _selected_leagues()
        sel_ids = [lg.id for lg in sel_leagues]
        teams = teams_query_for_league(sel_ids).order_by(Team.name).all()
        leagues = sel_leagues if sel_leagues else League.query.order_by(League.name).all()

        if request.method == "GET":
            return render_template("import_ics.html", teams=teams, leagues=leagues)

        action = request.form.get("action", "parse")

        if action == "parse":
            our_team_id = request.form.get("our_team_id", "").strip()
            if not our_team_id:
                flash("Please select a team.", "error")
                return render_template("import_ics.html", teams=teams, leagues=leagues)

            ics_file = request.files.get("ics_file")
            if not ics_file or not ics_file.filename:
                flash("Please upload a .ics calendar file.", "error")
                return render_template("import_ics.html", teams=teams, leagues=leagues)

            if not ics_file.filename.lower().endswith(".ics"):
                flash("File must be a .ics calendar file.", "error")
                return render_template("import_ics.html", teams=teams, leagues=leagues)

            try:
                our_team = Team.query.get(int(our_team_id))
                events = _parse_ics(ics_file.read(), our_team.name)
            except Exception as e:
                flash(f"Error parsing file: {e}", "error")
                return render_template("import_ics.html", teams=teams, leagues=leagues)

            if not events:
                flash("No events with a date and title were found in the file.", "warning")
                return render_template("import_ics.html", teams=teams, leagues=leagues)

            def _norm_name(s):
                return " ".join(re.sub(r"[()]", "", s.lower()).split())

            # Scope opponent auto-matching to our team's own league so an
            # identically-named team from a different season (e.g. "Yarm A
            # Men" in both Summer and Autumn) can't be matched by mistake.
            # Teams with no division/league yet (not fully set up) fall back
            # to a club-wide search.
            if our_team.division and our_team.division.league_id:
                all_teams_list = teams_query_for_league(our_team.division.league_id).all()
            else:
                all_teams_list = Team.query.all()
            for event in events:
                home_norm = _norm_name(event.get("home_str", ""))
                away_norm = _norm_name(event.get("away_str", ""))
                home_match = None
                away_match = None
                for t in all_teams_list:
                    t_norm = _norm_name(t.name)
                    if (
                        home_norm
                        and home_match is None
                        and (t_norm in home_norm or home_norm in t_norm)
                    ):
                        home_match = t
                    if (
                        away_norm
                        and away_match is None
                        and (t_norm in away_norm or away_norm in t_norm)
                    ):
                        away_match = t
                event["home_team"] = home_match
                event["away_team"] = away_match
                event["is_internal"] = home_match is not None and away_match is not None

            return render_template(
                "import_ics_review.html",
                events=events,
                our_team=our_team,
                all_teams=all_teams_list,
            )

        elif action == "confirm":
            our_team_id = int(request.form.get("our_team_id"))
            event_count = int(request.form.get("event_count", 0))
            created = 0
            skipped = 0
            try:
                for i in range(event_count):
                    if not request.form.get(f"include_{i}"):
                        skipped += 1
                        continue
                    date_str = request.form.get(f"date_{i}", "").strip()
                    if not date_str:
                        skipped += 1
                        continue
                    fixture_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    is_internal = request.form.get(f"is_internal_{i}") == "1"
                    if is_internal:
                        home_id = request.form.get(f"internal_home_id_{i}", type=int)
                        away_id = request.form.get(f"internal_away_id_{i}", type=int)
                        home_t = Team.query.get(home_id) if home_id else None
                        away_t = Team.query.get(away_id) if away_id else None
                        home_str = home_t.name if home_t else ""
                        away_str = away_t.name if away_t else ""
                    else:
                        our_side = request.form.get(f"our_side_{i}", "home")
                        home_str = request.form.get(f"home_str_{i}", "").strip()
                        away_str = request.form.get(f"away_str_{i}", "").strip()
                        opponent_id = request.form.get(f"opponent_team_id_{i}", type=int) or None
                        home_id = our_team_id if our_side == "home" else opponent_id
                        away_id = our_team_id if our_side == "away" else opponent_id
                    # Dedup: check both directions (handles re-importing either team's calendar)
                    if (
                        home_id
                        and away_id
                        and Fixture.query.filter(
                            Fixture.date == fixture_date,
                            db.or_(
                                db.and_(
                                    Fixture.home_team_id == home_id, Fixture.away_team_id == away_id
                                ),
                                db.and_(
                                    Fixture.home_team_id == away_id, Fixture.away_team_id == home_id
                                ),
                            ),
                        ).first()
                    ):
                        skipped += 1
                        continue
                    inferred_league_id = None
                    inferred_start_date = None
                    for tid in (home_id, away_id):
                        if tid:
                            t = Team.query.get(tid)
                            if t and t.division and t.division.league:
                                inferred_league_id = t.division.league_id
                                inferred_start_date = t.division.league.start_date
                                break
                    league_obj = (
                        League.query.get(inferred_league_id) if inferred_league_id else None
                    )
                    fixture = Fixture(
                        date=fixture_date,
                        home_team_id=home_id,
                        away_team_id=away_id,
                        home_team_name=home_str or None,
                        away_team_name=away_str or None,
                        league_id=inferred_league_id,
                        round_label=fixture_round_label(
                            fixture_date,
                            inferred_start_date,
                            league_obj.catchup_week_start_dates if league_obj else None,
                        ),
                    )
                    db.session.add(fixture)
                    db.session.flush()
                    for slot in build_rubber_plan(league_obj):
                        db.session.add(
                            Rubber(
                                fixture_id=fixture.id,
                                rubber_number=slot.rubber_number,
                                rubber_type="singles",
                            )
                        )
                    created += 1
                db.session.commit()
                flash(
                    f"Imported {created} fixture(s)." + (f" {skipped} skipped." if skipped else ""),
                    "success",
                )
            except Exception as e:
                db.session.rollback()
                flash(f"Error importing fixtures: {e}", "error")
            return redirect(url_for("list_fixtures"))

        flash("Unknown action.", "error")
        return redirect(url_for("list_fixtures"))

    def _autumn_pairs_plan(league):
        """Cleveland Autumn multi-pair rubber plan for manual_fixture_entry.html.

        Returns {"n_pairs": int, "plan": [{"rubber_number", "label"}, ...]} —
        the pair-major stride build_rubber_plan()/fixture_detail's Pairs grid
        already use — or None when there's nothing to auto-structure (a fixed
        -squad format, or n_pairs<=1 where physical order can't be ambiguous).
        manual_fixture_entry's plain "Rubber #N" cards have no concept of this
        stride, so an admin transcribing a paper scoresheet in natural
        chronological (round-major) order silently cross-attributes rubbers
        between pairs; the template uses this plan to auto-generate correctly
        labelled, correctly ordered, order-locked cards instead.
        """
        fmt = league.format if league else LEAGUE_FORMAT_CLEVELAND_AUTUMN
        if fmt in (LEAGUE_FORMAT_CLEVELAND_VETERANS, LEAGUE_FORMAT_NE_WINTER):
            return None
        n_pairs = (league.pairs_per_round if league else None) or 3
        if n_pairs <= 1:
            return None
        return {
            "n_pairs": n_pairs,
            "plan": [
                {"rubber_number": slot.rubber_number, "label": slot.label}
                for slot in build_rubber_plan(league)
            ],
        }

    @app.route("/fixtures/manual", methods=["GET", "POST"])
    def manual_fixture_entry():
        """Allow manual entry of fixtures without image parsing.

        Also serves as the match-day confirm step: when a fixture_id is supplied
        (query param on GET, hidden field on POST), this updates that existing
        fixture's rubbers in place — using the exact same save logic as a brand
        new manual entry — instead of creating a new fixture.
        """
        fixture_id = request.values.get("fixture_id", type=int)
        confirm_fixture = Fixture.query.get_or_404(fixture_id) if fixture_id else None

        if confirm_fixture:
            err = _require_captain_for_fixture(confirm_fixture)
        else:
            err = _require_admin()
        if err:
            return err

        # Player registration is admin-only: a captain confirming a pitchside
        # scoresheet may only attach existing players, never silently create new
        # ones via _get_or_create_player's name-matching fallback.
        is_admin_actor = session.get("user_role") == "admin"

        if request.method == "POST":
            try:
                if confirm_fixture:
                    fixture = confirm_fixture
                    home_team = fixture.home_team
                    away_team = fixture.away_team
                    league_id = fixture.league_id
                    for r in fixture.rubbers.all():
                        db.session.delete(r)
                    db.session.flush()
                else:
                    fixture_date = request.form.get("fixture_date")
                    league_id = request.form.get("league_id", type=int)
                    home_team_id = request.form.get("home_team_id", type=int)
                    away_team_id = request.form.get("away_team_id", type=int)

                    if not all([fixture_date, home_team_id, away_team_id]):
                        flash("Date and both teams are required.", "error")
                        return redirect(url_for("manual_fixture_entry"))

                    # Parse date
                    try:
                        fixture_date = datetime.strptime(fixture_date, "%Y-%m-%d").date()
                    except (ValueError, TypeError):
                        flash("Invalid date format.", "error")
                        return redirect(url_for("manual_fixture_entry"))

                    home_team = Team.query.get_or_404(home_team_id)
                    away_team = Team.query.get_or_404(away_team_id)

                    league_obj = League.query.get(league_id) if league_id else None
                    computed_round = fixture_round_label(
                        fixture_date,
                        league_obj.start_date if league_obj else None,
                        league_obj.catchup_week_start_dates if league_obj else None,
                    )

                    # Create fixture
                    fixture = Fixture(
                        date=fixture_date,
                        league_id=league_id,
                        home_team_id=home_team.id,
                        away_team_id=away_team.id,
                        round_label=computed_round,
                    )
                    db.session.add(fixture)
                    db.session.flush()

                # home_team/away_team may be None for a confirmed fixture whose
                # opponent isn't a tracked Team row (external club, name-only side).
                home_team_id_safe = home_team.id if home_team else None
                away_team_id_safe = away_team.id if away_team else None
                home_team_label = (
                    home_team.name if home_team else (fixture.home_team_name or "Home")
                )
                away_team_label = (
                    away_team.name if away_team else (fixture.away_team_name or "Away")
                )

                home_score = 0
                away_score = 0
                eligibility_warnings = []
                home_played_ids = set()
                away_played_ids = set()

                # Process rubbers from form
                rubber_count = int(request.form.get("rubber_count", 0))

                def _sv(key):
                    v = (request.form.get(key) or "").strip()
                    return int(v) if v else None

                for i in range(rubber_count):
                    rubber_num = i + 1
                    rubber_type = request.form.get(f"rubber_{i}_type", "singles")

                    rubber = Rubber(
                        fixture_id=fixture.id,
                        rubber_number=rubber_num,
                        rubber_type=rubber_type,
                    )

                    walkover = request.form.get(f"rubber_{i}_walkover") or None
                    if walkover not in ("home", "away"):
                        walkover = None

                    if walkover:
                        rubber.home_set1, rubber.away_set1 = (
                            (8, 0) if walkover == "home" else (0, 8)
                        )
                        rubber.set1_walkover = walkover
                        rubber.winner = walkover
                        if walkover == "home":
                            home_score += 1
                        else:
                            away_score += 1
                    else:
                        # Get set scores. set3 doubles as the champion tiebreak score
                        # for cleveland_autumn, decisive only when sets1/2 split 1-1.
                        rubber.home_set1 = _sv(f"rubber_{i}_home_set1")
                        rubber.away_set1 = _sv(f"rubber_{i}_away_set1")
                        rubber.home_set2 = _sv(f"rubber_{i}_home_set2")
                        rubber.away_set2 = _sv(f"rubber_{i}_away_set2")
                        rubber.home_set3 = _sv(f"rubber_{i}_home_set3")
                        rubber.away_set3 = _sv(f"rubber_{i}_away_set3")

                        rubber.winner = rubber_winner_from_sets(
                            rubber.home_set1,
                            rubber.away_set1,
                            rubber.home_set2,
                            rubber.away_set2,
                            rubber.home_set3,
                            rubber.away_set3,
                        )
                        if rubber.winner == "home":
                            home_score += 1
                        elif rubber.winner == "away":
                            away_score += 1

                    db.session.add(rubber)
                    db.session.flush()

                    # Add home players. A team_id of None here means that side
                    # isn't one of our own tracked Team rows — an untracked/
                    # external opponent, named only via home_team_name — so
                    # free-typed names on that side are opposition players and
                    # must never be turned into (or matched against) real
                    # Player records; they exist only for on-screen reference.
                    home_players_str = request.form.get(f"rubber_{i}_home_players", "")
                    if home_team_id_safe:
                        for player_name in [
                            p.strip() for p in home_players_str.split(",") if p.strip()
                        ]:
                            player = _get_or_create_player(
                                player_name, home_team_id_safe, allow_create=is_admin_actor
                            )
                            if league_id:
                                elig = check_player_eligibility(
                                    player.id, home_team_id_safe, league_id
                                )
                                if not elig["eligible"]:
                                    eligibility_warnings.append(
                                        f"⚠️ {player_name} is already tied to a "
                                        f"different team in this league"
                                    )
                            result = PlayerMatchResult(
                                player_id=player.id,
                                rubber_id=rubber.id,
                                side="home",
                                won=(rubber.winner == "home"),
                            )
                            db.session.add(result)
                            home_played_ids.add(player.id)

                    # Add away players — same opposition guard as above.
                    away_players_str = request.form.get(f"rubber_{i}_away_players", "")
                    if away_team_id_safe:
                        for player_name in [
                            p.strip() for p in away_players_str.split(",") if p.strip()
                        ]:
                            player = _get_or_create_player(
                                player_name, away_team_id_safe, allow_create=is_admin_actor
                            )
                            if league_id:
                                elig = check_player_eligibility(
                                    player.id, away_team_id_safe, league_id
                                )
                                if not elig["eligible"]:
                                    eligibility_warnings.append(
                                        "⚠️ %s is already tied to a different team in "
                                        "this league" % player_name
                                    )
                            result = PlayerMatchResult(
                                player_id=player.id,
                                rubber_id=rubber.id,
                                side="away",
                                won=(rubber.winner == "away"),
                            )
                            db.session.add(result)
                            away_played_ids.add(player.id)

                _ensure_squad_entries(fixture, home_played_ids, home_team_id_safe)
                _ensure_squad_entries(fixture, away_played_ids, away_team_id_safe)
                _ensure_team_roster(home_played_ids, home_team_id_safe)
                _ensure_team_roster(away_played_ids, away_team_id_safe)

                # The loop above tallies 1 pt/rubber win (this route's own
                # long-standing convention) — always override with the same
                # scoring functions save_rubbers uses, so a manually-entered or
                # captain-confirmed pitchside fixture scores identically to one
                # entered live, for every format (including cleveland_autumn's
                # 2 pts/rubber + 4 bonus pts to the fixture winner).
                mfe_league = League.query.get(league_id) if league_id else None
                mfe_fmt = mfe_league.format if mfe_league else LEAGUE_FORMAT_CLEVELAND_AUTUMN
                mfe_rubbers = fixture.rubbers.order_by(Rubber.rubber_number).all()
                if mfe_fmt == LEAGUE_FORMAT_NE_WINTER:
                    mfe_plan_by_number = {
                        slot.rubber_number: slot for slot in build_rubber_plan(mfe_league)
                    }
                    home_score, away_score = score_from_set_points(
                        (r, mfe_plan_by_number[r.rubber_number])
                        for r in mfe_rubbers
                        if r.rubber_number in mfe_plan_by_number
                    )
                else:
                    home_score, away_score = score_from_rubber_results(mfe_rubbers)

                fixture.home_score = home_score
                fixture.away_score = away_score
                _touch_scores_updated()
                db.session.commit()

                # Check for new team commitments
                if league_id and home_team_id_safe:
                    restriction = LeagueRestriction.query.filter_by(league_id=league_id).first()
                    if restriction and restriction.team_commitment_threshold > 0:
                        all_players = set()
                        for i in range(rubber_count):
                            home_str = request.form.get(f"rubber_{i}_home_players", "")
                            all_players.update(
                                [p.strip() for p in home_str.split(",") if p.strip()]
                            )
                            # Away names only belong here when the away side is
                            # also one of our own tracked teams (both_yarm) —
                            # otherwise they're opposition players and must not
                            # be matched against, or committed to, our roster.
                            if away_team_id_safe:
                                away_str = request.form.get(f"rubber_{i}_away_players", "")
                                all_players.update(
                                    [p.strip() for p in away_str.split(",") if p.strip()]
                                )

                        for player_name in all_players:
                            player = _find_player_by_name(player_name)
                            if not player:
                                continue

                            elig = check_player_eligibility(player.id, home_team_id_safe, league_id)
                            committed_team_id = elig["effective_committed_team_id"]
                            if not committed_team_id:
                                continue
                            committed_team = Team.query.get(committed_team_id)

                            existing_commitment = PlayerTeamCommitment.query.filter_by(
                                player_id=player.id,
                                league_id=league_id,
                                season="2026",
                            ).first()
                            if not existing_commitment:
                                commitment = PlayerTeamCommitment(
                                    player_id=player.id,
                                    team_id=committed_team_id,
                                    league_id=league_id,
                                    season="2026",
                                )
                                db.session.add(commitment)
                                eligibility_warnings.append(
                                    f"✅ {player_name} tied to {committed_team.name} "
                                    "for the season!"
                                )
                            elif existing_commitment.team_id != committed_team_id:
                                # Upgrade commitment if this team is higher in hierarchy.
                                existing_rank = (
                                    _team_rank(existing_commitment.team)
                                    if existing_commitment.team
                                    else (float("inf"), float("inf"))
                                )
                                if _team_rank(committed_team) < existing_rank:
                                    existing_commitment.team_id = committed_team_id
                                    eligibility_warnings.append(
                                        f"✅ {player_name} commitment upgraded to "
                                        f"{committed_team.name} — now tied to "
                                        f"{committed_team.name}."
                                    )

                        db.session.commit()

                if eligibility_warnings:
                    for warning in eligibility_warnings:
                        flash(warning, "warning")

                if confirm_fixture:
                    draft = MatchDayDraft.query.filter_by(fixture_id=fixture.id).first()
                    if draft:
                        draft.status = "confirmed"
                        draft.confirmed_at = datetime.utcnow()
                        db.session.commit()

                flash(
                    f"Fixture saved: {home_team_label} {home_score} – {away_score} "
                    f"{away_team_label}",
                    "success",
                )
                return redirect(url_for("fixture_detail", fixture_id=fixture.id))

            except Exception as e:
                db.session.rollback()
                flash(f"Error saving fixture: {str(e)}", "error")
                redirect_kwargs = {"fixture_id": fixture_id} if fixture_id else {}
                return redirect(url_for("manual_fixture_entry", **redirect_kwargs))

        # GET: Show form
        teams = Team.query.order_by(Team.name).all()
        leagues = League.query.order_by(League.name).all()
        prefill_rubbers = []
        draft = None
        if confirm_fixture:
            draft = MatchDayDraft.query.filter_by(fixture_id=confirm_fixture.id).first()
            if draft and draft.data:
                try:
                    raw_rubbers = json.loads(draft.data).get("rubbers", [])
                except ValueError:
                    raw_rubbers = []
                for r in raw_rubbers:
                    prefill_rubbers.append(
                        {
                            "type": r.get("type", "singles"),
                            "home_players": ", ".join(r.get("home_players") or []),
                            "away_players": ", ".join(r.get("away_players") or []),
                            "home_set1": r.get("home_set1"),
                            "away_set1": r.get("away_set1"),
                            "home_set2": r.get("home_set2"),
                            "away_set2": r.get("away_set2"),
                            "home_set3": r.get("home_set3"),
                            "away_set3": r.get("away_set3"),
                            "walkover": r.get("walkover"),
                        }
                    )
        all_player_names = [
            p.name
            for p in Player.query.filter_by(membership_status="active")
            .order_by(Player.first_name, Player.last_name)
            .all()
        ]
        league_pairs_plans = {}
        for lg in leagues:
            plan_info = _autumn_pairs_plan(lg)
            if plan_info:
                league_pairs_plans[lg.id] = plan_info
        confirm_pairs_plan = (
            _autumn_pairs_plan(_effective_league(confirm_fixture)) if confirm_fixture else None
        )
        return render_template(
            "manual_fixture_entry.html",
            teams=teams,
            leagues=leagues,
            confirm_fixture=confirm_fixture,
            draft=draft,
            prefill_rubbers_json=json.dumps(prefill_rubbers),
            all_player_names_json=json.dumps(all_player_names),
            league_pairs_plans_json=json.dumps(league_pairs_plans),
            confirm_pairs_plan_json=json.dumps(confirm_pairs_plan),
        )

    @app.route("/fixtures/<int:fixture_id>")
    def fixture_detail(fixture_id):
        fixture = Fixture.query.get_or_404(fixture_id)
        rubbers = fixture.rubbers.order_by(Rubber.rubber_number).all()
        leagues = League.query.order_by(League.name).all()
        all_teams = Team.query.order_by(Team.name).all()
        home_ps = (
            list(fixture.home_team.players.order_by(Player.last_name, Player.first_name).all())
            if fixture.home_team
            else []
        )
        away_ps = (
            list(fixture.away_team.players.order_by(Player.last_name, Player.first_name).all())
            if fixture.away_team
            else []
        )

        # Determine which Yarm team's perspective to show.
        team_id = request.args.get("team_id", type=int)
        both_yarm = fixture.home_team_id is not None and fixture.away_team_id is not None

        if team_id and team_id == fixture.home_team_id:
            yarm_is_home = True
            yarm_team = fixture.home_team
        elif team_id and team_id == fixture.away_team_id:
            yarm_is_home = False
            yarm_team = fixture.away_team
        else:
            # No explicit team_id — default to the captain's team when unambiguous.
            captain_ids = _get_captain_team_ids()
            if (
                both_yarm
                and fixture.away_team_id in captain_ids
                and fixture.home_team_id not in captain_ids
            ):
                yarm_is_home = False
                yarm_team = fixture.away_team
            else:
                yarm_is_home = fixture.home_team_id is not None
                yarm_team = fixture.home_team if yarm_is_home else fixture.away_team

        # ── League / round context ────────────────────────────────────────
        eff_league = _effective_league(fixture)
        round_label = fixture.round_label or fixture_round_label(
            fixture.date,
            eff_league.start_date if eff_league else None,
            eff_league.catchup_week_start_dates if eff_league else None,
        )
        _restriction = (
            LeagueRestriction.query.filter_by(league_id=eff_league.id).first()
            if eff_league
            else None
        )
        _num_early = _restriction.early_season_weeks if _restriction else 0
        _early_round_labels = {f"Round {r}" for r in range(1, _num_early + 1)}
        is_early_round = round_label in _early_round_labels

        # Week conflict only enforced during early-season rounds; after that,
        # players may appear for multiple teams in the same week.
        apply_week_conflict = eff_league is not None and is_early_round

        league_format = eff_league.format if eff_league else LEAGUE_FORMAT_CLEVELAND_AUTUMN
        rubber_plan = build_rubber_plan(eff_league)
        if league_format in (LEAGUE_FORMAT_CLEVELAND_VETERANS, LEAGUE_FORMAT_NE_WINTER):
            squad_size = 4
        else:
            squad_size = (
                eff_league.pairs_per_round * 2 if (eff_league and eff_league.pairs_per_round) else 6
            )
        # Mirrors build_rubber_plan()'s own n_pairs derivation exactly, so the
        # grid's dimensions always match the plan's shape regardless of how many
        # Rubber rows happen to already exist in the DB.
        n_pairs = (eff_league.pairs_per_round if eff_league else None) or 3
        autumn_sets = rubber_plan[0].sets if rubber_plan else 2
        autumn_parent = eff_league.parent_league if eff_league else None
        autumn_games_per_set = (
            autumn_parent.games_per_set if autumn_parent and autumn_parent.games_per_set else 6
        )

        # For the two fixed-squad formats, prefill the "pick 2 mixed pairs" picker
        # from whichever rubber's home/away slots are the symmetric "pair 1 v pair
        # 1" / "pair 2 v pair 2" ones — the same slot key set applies to either
        # perspective for those two rubbers, unlike Veterans' cross rubbers.
        squad_prefill = {
            "pair1_man": None,
            "pair1_woman": None,
            "pair2_man": None,
            "pair2_woman": None,
        }
        _fixed_squad_formats = (LEAGUE_FORMAT_CLEVELAND_VETERANS, LEAGUE_FORMAT_NE_WINTER)
        if league_format in _fixed_squad_formats and yarm_team:
            yarm_side_for_prefill = "home" if yarm_is_home else "away"
            rubbers_by_number = {r.rubber_number: r for r in rubbers}
            for slot in rubber_plan:
                my_slots = slot.home_slots if yarm_is_home else slot.away_slots
                if my_slots not in (
                    ("pair1_man", "pair1_woman"),
                    ("pair2_man", "pair2_woman"),
                ):
                    continue
                rubber = rubbers_by_number.get(slot.rubber_number)
                if not rubber:
                    continue
                for pmr in rubber.player_results.filter_by(side=yarm_side_for_prefill).all():
                    player = Player.query.get(pmr.player_id)
                    if not player:
                        continue
                    if player.gender == "M" and squad_prefill[my_slots[0]] is None:
                        squad_prefill[my_slots[0]] = player.id
                    elif player.gender == "F" and squad_prefill[my_slots[1]] is None:
                        squad_prefill[my_slots[1]] = player.id

        # ── Squad IDs — scoped per team for dual-Yarm fixtures ───────────
        if both_yarm:
            home_existing_squad_ids = {
                e.player_id
                for e in FixtureSquadEntry.query.filter_by(
                    fixture_id=fixture_id, team_id=fixture.home_team_id
                ).all()
            }
            away_existing_squad_ids = {
                e.player_id
                for e in FixtureSquadEntry.query.filter_by(
                    fixture_id=fixture_id, team_id=fixture.away_team_id
                ).all()
            }
            existing_squad_ids = (
                home_existing_squad_ids if yarm_is_home else away_existing_squad_ids
            )
        else:
            home_existing_squad_ids = set()
            away_existing_squad_ids = set()
            existing_squad_ids = {
                e.player_id for e in FixtureSquadEntry.query.filter_by(fixture_id=fixture_id).all()
            }

        # ── Build squad pool for a given team ────────────────────────────
        fixture_played = fixture.home_score != 0 or fixture.away_score != 0
        league_id = eff_league.id if eff_league else None

        def _build_squad_pool(team, team_existing_ids):
            pool = []
            if not team:
                return pool
            seen_ids = set()
            for p in team.players.order_by(Player.last_name, Player.first_name).all():
                elig = (
                    check_player_eligibility(p.id, team.id, league_id)
                    if league_id
                    else {
                        "eligible": True,
                        "commitment_info": None,
                        "restriction_description": None,
                    }
                )
                week_conflict = apply_week_conflict and check_week_conflict(
                    p.id, fixture.date, fixture_id
                )
                # A week conflict only counts within the same league — playing
                # in a different league the same week is permitted.
                if week_conflict and league_id:
                    cf_league_id = week_conflict.league_id
                    if (
                        not cf_league_id
                        and week_conflict.home_team
                        and week_conflict.home_team.division
                    ):
                        cf_league_id = week_conflict.home_team.division.league_id
                    if (
                        not cf_league_id
                        and week_conflict.away_team
                        and week_conflict.away_team.division
                    ):
                        cf_league_id = week_conflict.away_team.division.league_id
                    if cf_league_id != league_id:
                        week_conflict = None
                is_active = p.membership_status == "active"
                selectable = elig["eligible"] and not week_conflict and is_active
                if not is_active:
                    reason = "Player is inactive"
                elif not elig["eligible"]:
                    reason = elig.get("restriction_description") or "Tied to another team"
                elif week_conflict:
                    cf = week_conflict  # conflicting Fixture object
                    home = cf.home_team.name if cf.home_team else (cf.home_team_name or "?")
                    away = cf.away_team.name if cf.away_team else (cf.away_team_name or "?")
                    date_str = cf.date.strftime("%-d %b") if cf.date else ""
                    reason = f"Selected in {home} vs {away}" + (
                        f" ({date_str})" if date_str else ""
                    )
                else:
                    reason = None
                threshold = elig.get("commitment_threshold")
                current = elig.get("current_fixtures", 0)
                would_commit = bool(
                    not fixture_played
                    and threshold
                    and current == threshold - 1
                    and not elig.get("commitment_info")
                    and elig["eligible"]
                )
                seen_ids.add(p.id)
                pool.append(
                    {
                        "player": p,
                        "selectable": selectable,
                        "in_squad": p.id in team_existing_ids,
                        "reason": reason,
                        "commitment_info": elig.get("commitment_info"),
                        "would_commit": would_commit,
                    }
                )
            # Include players added via weekly allocation who aren't on the team roster.
            for pid in sorted(team_existing_ids - seen_ids):
                p = Player.query.get(pid)
                if not p:
                    continue
                elig = (
                    check_player_eligibility(p.id, team.id, league_id)
                    if league_id
                    else {
                        "eligible": True,
                        "commitment_info": None,
                        "restriction_description": None,
                    }
                )
                pool.append(
                    {
                        "player": p,
                        "selectable": elig["eligible"],
                        "in_squad": True,
                        "reason": (
                            elig.get("restriction_description") if not elig["eligible"] else None
                        ),
                        "commitment_info": elig.get("commitment_info"),
                        "would_commit": False,
                    }
                )
            pool.sort(key=lambda e: (e["player"].last_name, e["player"].first_name))
            return pool

        squad_pool = _build_squad_pool(yarm_team, existing_squad_ids)

        # For dual-Yarm, also build the other team's pool so the template can
        # show a link / info for the other team's squad management.
        if both_yarm:
            other_team = fixture.away_team if yarm_is_home else fixture.home_team
            other_existing_ids = (
                away_existing_squad_ids if yarm_is_home else home_existing_squad_ids
            )
            other_squad_pool = _build_squad_pool(other_team, other_existing_ids)
        else:
            other_team = None
            other_existing_ids = set()
            other_squad_pool = []

        # Restrict pairs dropdowns to squad members when a squad has been selected
        if both_yarm:
            eligible_players = home_ps if yarm_is_home else away_ps
        else:
            seen_ids = {p.id for p in home_ps}
            eligible_players = home_ps + [p for p in away_ps if p.id not in seen_ids]

        # Inactive players can't be newly picked, but stay selectable here if they're
        # already reserved in the squad or already recorded on one of this fixture's
        # rubbers, so an existing pick doesn't silently disappear from the dropdown.
        recorded_player_ids = {
            pmr.player_id for rubber in rubbers for pmr in rubber.player_results.all()
        }
        eligible_players = [
            p
            for p in eligible_players
            if p.membership_status == "active"
            or p.id in existing_squad_ids
            or p.id in recorded_player_ids
        ]

        if existing_squad_ids:
            elig_ids = {p.id for p in eligible_players}
            eligible_players = [p for p in eligible_players if p.id in existing_squad_ids]
            # Add squad members who came from weekly allocation (not on the team roster).
            for pid in sorted(existing_squad_ids - elig_ids):
                p = Player.query.get(pid)
                if p:
                    eligible_players.append(p)

        # Fee section: squad members + players who have played for yarm team.
        fee_player_ids = set(existing_squad_ids)
        if yarm_team:
            yarm_side = "home" if yarm_is_home else "away"
            for rubber in rubbers:
                for pmr in rubber.player_results.filter_by(side=yarm_side).all():
                    fee_player_ids.add(pmr.player_id)
        fee_players = []
        seen_fee = set()
        for entry in squad_pool:
            if entry["player"].id in fee_player_ids:
                fee_players.append(entry["player"])
                seen_fee.add(entry["player"].id)
        for pid in sorted(fee_player_ids - seen_fee):
            p = Player.query.get(pid)
            if p:
                fee_players.append(p)
        fee_paid_ids = {
            r.player_id
            for r in MatchFeePaid.query.filter_by(fixture_id=fixture_id, team_id=team_id).all()
        }

        return render_template(
            "fixture_detail.html",
            fixture=fixture,
            rubbers=rubbers,
            leagues=leagues,
            all_teams=all_teams,
            eligible_players=eligible_players,
            yarm_is_home=yarm_is_home,
            yarm_team=yarm_team,
            squad_pool=squad_pool,
            squad_size=squad_size,
            league_format=league_format,
            rubber_plan=rubber_plan,
            n_pairs=n_pairs,
            autumn_sets=autumn_sets,
            autumn_games_per_set=autumn_games_per_set,
            squad_prefill=squad_prefill,
            existing_squad_ids=existing_squad_ids,
            is_early_round=is_early_round,
            team_id=team_id,
            both_yarm=both_yarm,
            other_team=other_team,
            other_squad_pool=other_squad_pool,
            other_existing_squad_ids=other_existing_ids,
            fee_players=fee_players,
            fee_paid_ids=fee_paid_ids,
            match_day_draft=MatchDayDraft.query.filter_by(fixture_id=fixture_id).first(),
        )

    @app.route("/fixtures/<int:fixture_id>/squad", methods=["POST"])
    def save_fixture_squad(fixture_id):
        """Replace the squad reservation list for a fixture."""
        fixture = Fixture.query.get_or_404(fixture_id)
        err = _require_captain_for_fixture(fixture)
        if err:
            return err
        selected_ids = set(request.form.getlist("squad_player_ids", type=int))
        team_id = request.form.get("team_id", type=int)
        both_yarm = fixture.home_team_id is not None and fixture.away_team_id is not None
        # Must replace exactly the rows fixture_detail displayed as this squad, or a
        # leftover row (e.g. team_id NULL) collides with uq_fixture_player_squad on re-insert.
        existing_q = FixtureSquadEntry.query.filter_by(fixture_id=fixture_id)
        other_team_ids = set()
        if both_yarm and team_id:
            # Dual-Yarm: only replace this team's entries (plus untagged legacy rows,
            # which neither side's view shows), preserving the other team's.
            existing_q = existing_q.filter(
                db.or_(FixtureSquadEntry.team_id == team_id, FixtureSquadEntry.team_id.is_(None))
            )
            other_team_ids = {
                e.player_id
                for e in FixtureSquadEntry.query.filter(
                    FixtureSquadEntry.fixture_id == fixture_id,
                    FixtureSquadEntry.team_id.isnot(None),
                    FixtureSquadEntry.team_id != team_id,
                ).all()
            }
        previous_ids = {e.player_id for e in existing_q.all()}
        existing_q.delete(synchronize_session=False)
        skipped = []
        with db.session.no_autoflush:
            for pid in selected_ids:
                player = Player.query.get(pid)
                if not player:
                    continue
                if pid in other_team_ids:
                    # Already reserved by the other Yarm team in this fixture.
                    skipped.append(player.name)
                    continue
                # An inactive player can't be newly added, but stays if they were already
                # reserved (e.g. before being deactivated) and the form re-submits them.
                if player.membership_status == "active" or pid in previous_ids:
                    db.session.add(
                        FixtureSquadEntry(fixture_id=fixture_id, player_id=pid, team_id=team_id)
                    )
        db.session.commit()
        flash("Squad saved.", "success")
        if skipped:
            flash(
                "Not added (already in the other team's squad for this fixture): "
                + ", ".join(sorted(skipped)),
                "warning",
            )
        redirect_kwargs = {"fixture_id": fixture_id}
        if team_id:
            redirect_kwargs["team_id"] = team_id
        return redirect(url_for("fixture_detail", **redirect_kwargs))

    @app.route("/fixtures/<int:fixture_id>/squad/add", methods=["POST"])
    def add_fixture_squad_player(fixture_id):
        """Add a single player to a fixture squad from the weekly allocation view."""
        fixture = Fixture.query.get_or_404(fixture_id)
        err = _require_captain_for_fixture(fixture)
        if err:
            return err
        player_id = request.form.get("player_id", type=int)
        team_id = request.form.get("team_id", type=int)
        league_id = request.form.get("league_id", type=int)
        player = Player.query.get(player_id) if player_id else None
        if player and player.membership_status == "active":
            exists = FixtureSquadEntry.query.filter_by(
                fixture_id=fixture_id, player_id=player_id
            ).first()
            if not exists:
                db.session.add(
                    FixtureSquadEntry(fixture_id=fixture_id, player_id=player_id, team_id=team_id)
                )
                db.session.commit()
        redirect_url = url_for("list_teams")
        if league_id:
            redirect_url += f"?league={league_id}#allocation-section"
        return redirect(redirect_url)

    @app.route("/fixtures/<int:fixture_id>/fees", methods=["POST"])
    def save_match_fees(fixture_id):
        """Save match fee payment status for a fixture's squad."""
        fixture = Fixture.query.get_or_404(fixture_id)
        err = _require_captain_for_fixture(fixture)
        if err:
            return err
        team_id = request.form.get("team_id", type=int)
        paid_ids = set(request.form.getlist("fee_paid_ids", type=int))
        if team_id:
            MatchFeePaid.query.filter_by(fixture_id=fixture_id, team_id=team_id).delete()
        else:
            MatchFeePaid.query.filter_by(fixture_id=fixture_id, team_id=None).delete()
        for pid in paid_ids:
            if Player.query.get(pid):
                db.session.add(MatchFeePaid(fixture_id=fixture_id, player_id=pid, team_id=team_id))
        db.session.commit()
        flash("Match fees saved.", "success")
        redirect_kwargs = {"fixture_id": fixture_id}
        if team_id:
            redirect_kwargs["team_id"] = team_id
        return redirect(url_for("fixture_detail", **redirect_kwargs))

    @app.route("/fixtures/<int:fixture_id>/reschedule", methods=["POST"])
    def reschedule_fixture(fixture_id):
        """Move a postponed fixture to a new date, keeping its original round_label."""
        fixture = Fixture.query.get_or_404(fixture_id)
        err = _require_captain_for_fixture(fixture)
        if err:
            return jsonify({"error": "Permission denied"}), 403
        new_date_str = (request.form.get("new_date") or "").strip()
        try:
            new_date = datetime.strptime(new_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid date"}), 400
        if not fixture.round_label and fixture.league:
            lg = fixture.league
            fixture.round_label = fixture_round_label(
                fixture.date,
                lg.start_date,
                lg.catchup_week_start_dates,
            )
        fixture.date = new_date
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/fixtures/<int:fixture_id>/edit", methods=["POST"])
    def edit_fixture(fixture_id):
        fixture = Fixture.query.get_or_404(fixture_id)
        err = _require_captain_for_fixture(fixture)
        if err:
            return err
        try:
            fixture_date = request.form.get("fixture_date")
            league_id = request.form.get("league_id", type=int)

            if not fixture_date:
                flash("Date is required.", "error")
                return redirect(url_for("fixture_detail", fixture_id=fixture_id))

            # Parse date
            try:
                fixture_date = datetime.strptime(fixture_date, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                flash("Invalid date format.", "error")
                return redirect(url_for("fixture_detail", fixture_id=fixture_id))

            fixture.date = fixture_date
            fixture.league_id = league_id if league_id else None
            fixture.home_team_id = request.form.get("home_team_id", type=int) or None
            fixture.away_team_id = request.form.get("away_team_id", type=int) or None

            walkover_winner = request.form.get("walkover_winner") or None
            if walkover_winner not in ("home", "away"):
                walkover_winner = None
            fixture.walkover_winner = walkover_winner

            if walkover_winner:
                wo_league = _effective_league(fixture)
                max_score = walkover_max_score(wo_league)
                fixture.home_score = max_score if walkover_winner == "home" else 0
                fixture.away_score = max_score if walkover_winner == "away" else 0
            else:
                fixture.home_score = request.form.get("home_score", 0, type=int)
                fixture.away_score = request.form.get("away_score", 0, type=int)

            # Use manually supplied label if provided, otherwise recompute from league start date
            manual_label = request.form.get("round_label", "").strip()
            if manual_label:
                fixture.round_label = manual_label
            else:
                league_obj = League.query.get(league_id) if league_id else None
                fixture.round_label = fixture_round_label(
                    fixture_date,
                    league_obj.start_date if league_obj else None,
                    league_obj.catchup_week_start_dates if league_obj else None,
                )

            db.session.commit()
            flash("Fixture updated.", "success")
            return redirect(url_for("fixture_detail", fixture_id=fixture_id))
        except Exception as e:
            flash(f"Error updating fixture: {str(e)}", "error")
            return redirect(url_for("fixture_detail", fixture_id=fixture_id))

    def _effective_league(fixture):
        """Return the league a fixture belongs to for format/round-label purposes.

        Its own league_id if tagged, else either team's current division league.
        """
        return (
            fixture.league
            or (
                fixture.home_team.division.league
                if fixture.home_team and fixture.home_team.division
                else None
            )
            or (
                fixture.away_team.division.league
                if fixture.away_team and fixture.away_team.division
                else None
            )
        )

    def _ensure_squad_entries(fixture, player_ids, team_id):
        """Add FixtureSquadEntry rows for played players not already in the squad."""
        if not team_id:
            return
        existing = {
            e.player_id for e in FixtureSquadEntry.query.filter_by(fixture_id=fixture.id).all()
        }
        for pid in player_ids:
            if pid not in existing:
                db.session.add(
                    FixtureSquadEntry(fixture_id=fixture.id, player_id=pid, team_id=team_id)
                )
                existing.add(pid)

    def _ensure_team_roster(player_ids, team_id):
        """Add to player_teams any played players not already on the team roster."""
        if not team_id:
            return
        team = Team.query.get(team_id)
        if not team:
            return
        existing_ids = {p.id for p in team.players.all()}
        for pid in player_ids:
            if pid not in existing_ids:
                player = Player.query.get(pid)
                if player:
                    team.players.append(player)
                    existing_ids.add(pid)

    def _ensure_team_commitments(fixture, player_ids, team_id):
        """Create/upgrade PlayerTeamCommitment records for players who crossed the threshold."""
        if not team_id or not fixture.league_id or not player_ids:
            return
        restriction = LeagueRestriction.query.filter_by(league_id=fixture.league_id).first()
        if not restriction or not restriction.team_commitment_threshold:
            return
        for pid in player_ids:
            elig = check_player_eligibility(pid, team_id, fixture.league_id)
            committed_team_id = elig["effective_committed_team_id"]
            if not committed_team_id:
                continue
            existing = PlayerTeamCommitment.query.filter_by(
                player_id=pid, league_id=fixture.league_id, season="2026"
            ).first()
            if not existing:
                db.session.add(
                    PlayerTeamCommitment(
                        player_id=pid,
                        team_id=committed_team_id,
                        league_id=fixture.league_id,
                        season="2026",
                    )
                )
            elif existing.team_id != committed_team_id:
                existing_rank = (
                    _team_rank(existing.team) if existing.team else (float("inf"), float("inf"))
                )
                new_rank = _team_rank(Team.query.get(committed_team_id))
                if new_rank < existing_rank:
                    existing.team_id = committed_team_id

    def _save_rubbers_fixed_squad(fixture, league, team_id):
        """Save rubber scores for a fixed-squad format (cleveland_veterans / ne_winter).

        Unlike the pairs-rotation format, the plan is a small static list keyed
        directly by rubber_number for both perspectives — no rotation index to
        compute. The entering side posts 4 squad player IDs (2 mixed pairs);
        each rubber's 2 players are resolved via its RubberSlot's home_slots/
        away_slots, which differ between perspectives only for Veterans' two
        "cross" mixed rubbers (see build_rubber_plan).
        """
        fmt = league.format
        plan = build_rubber_plan(league)

        both_yarm = fixture.home_team_id is not None and fixture.away_team_id is not None
        if team_id and team_id == fixture.home_team_id:
            yarm_is_home = True
        elif team_id and team_id == fixture.away_team_id:
            yarm_is_home = False
        else:
            yarm_is_home = fixture.home_team_id is not None
        yarm_side = "home" if yarm_is_home else "away"
        # In a dual-Yarm fixture, only the home side may enter scores — mirrors
        # the pairs-rotation format's away-perspective read-only convention, so
        # the two captains can't submit conflicting numbers for the same rubber.
        away_perspective = both_yarm and not yarm_is_home

        def _sv(key):
            v = (request.form.get(key) or "").strip()
            return int(v) if v else None

        squad = {
            "pair1_man": request.form.get("squad_pair1_man", type=int),
            "pair1_woman": request.form.get("squad_pair1_woman", type=int),
            "pair2_man": request.form.get("squad_pair2_man", type=int),
            "pair2_woman": request.form.get("squad_pair2_woman", type=int),
        }
        redirect_kwargs = {"fixture_id": fixture.id}
        if team_id:
            redirect_kwargs["team_id"] = team_id
        if not all(squad.values()):
            flash("Select all 4 squad players (2 men, 2 women) before saving results.", "error")
            return redirect(url_for("fixture_detail", **redirect_kwargs))

        existing_rubbers = {
            r.rubber_number: r for r in fixture.rubbers.order_by(Rubber.rubber_number).all()
        }
        if both_yarm:
            for r in existing_rubbers.values():
                PlayerMatchResult.query.filter_by(rubber_id=r.id, side=yarm_side).delete()
            db.session.flush()
        else:
            for r in existing_rubbers.values():
                db.session.delete(r)
            db.session.flush()
            existing_rubbers = {}

        rubbers_in_order = []
        for slot in plan:
            rubber = existing_rubbers.get(slot.rubber_number)
            if rubber is None:
                rubber = Rubber(
                    fixture_id=fixture.id,
                    rubber_number=slot.rubber_number,
                    rubber_type="doubles",
                )
                db.session.add(rubber)
                db.session.flush()
                existing_rubbers[slot.rubber_number] = rubber
            rn = slot.rubber_number

            if not away_perspective:
                walkover = request.form.get(f"rubber_{rn}_walkover") or None
                if walkover not in ("home", "away"):
                    walkover = None

                for n in range(1, slot.sets + 1):
                    tied = False
                    h_score = a_score = None
                    if walkover:
                        if fmt != LEAGUE_FORMAT_NE_WINTER:
                            # cleveland_veterans: mirror the pairs-rotation format's
                            # convention of a fabricated 8-0 walkover scoreline,
                            # which then drives score_from_rubber_results' win
                            # comparison.
                            h_score, a_score = (8, 0) if walkover == "home" else (0, 8)
                        # ne_winter: leave set scores null — score_from_set_points
                        # reads set1_walkover directly and awards the full rubber.
                    else:
                        tied = request.form.get(f"rubber_{rn}_set{n}_tie") == "on"
                        if not tied:
                            left = _sv(f"rubber_{rn}_home_set{n}")
                            right = _sv(f"rubber_{rn}_away_set{n}")
                            h_score, a_score = (left, right) if yarm_is_home else (right, left)
                    setattr(rubber, f"home_set{n}", h_score)
                    setattr(rubber, f"away_set{n}", a_score)
                    if n == 1:
                        rubber.set1_tie = tied
                        rubber.set1_walkover = walkover

                if fmt != LEAGUE_FORMAT_NE_WINTER:
                    # cleveland_veterans — single-set rubber, compute winner now
                    # (score_from_rubber_results only reads .winner, never sets it).
                    if rubber.set1_tie:
                        rubber.winner = "tie"
                    elif rubber.home_set1 is not None and rubber.away_set1 is not None:
                        if rubber.home_set1 > rubber.away_set1:
                            rubber.winner = "home"
                        elif rubber.away_set1 > rubber.home_set1:
                            rubber.winner = "away"
                        else:
                            rubber.winner = None
                    else:
                        rubber.winner = None

            db.session.flush()
            rubbers_in_order.append((rubber, slot))

        if not away_perspective:
            if fmt == LEAGUE_FORMAT_NE_WINTER:
                # Also sets each rubber's .winner (2-0/0-2/1-1) as a side effect.
                fixture.home_score, fixture.away_score = score_from_set_points(rubbers_in_order)
            else:
                fixture.home_score, fixture.away_score = score_from_rubber_results(
                    rubber for rubber, _slot in rubbers_in_order
                )

        yarm_played_pids = set(squad.values())
        for rubber, slot in rubbers_in_order:
            yarm_won = rubber.winner is not None and rubber.winner == yarm_side
            my_slots = slot.home_slots if yarm_is_home else slot.away_slots
            for key in my_slots:
                db.session.add(
                    PlayerMatchResult(
                        player_id=squad[key],
                        rubber_id=rubber.id,
                        side=yarm_side,
                        won=yarm_won,
                    )
                )

        yarm_team_id = fixture.home_team_id if yarm_is_home else fixture.away_team_id
        _ensure_squad_entries(fixture, yarm_played_pids, yarm_team_id)
        _ensure_team_roster(yarm_played_pids, yarm_team_id)
        _ensure_team_commitments(fixture, yarm_played_pids, yarm_team_id)
        _touch_scores_updated()
        db.session.commit()
        flash("Results saved.", "success")
        return redirect(url_for("fixture_detail", **redirect_kwargs))

    @app.route("/fixtures/<int:fixture_id>/rubbers/save", methods=["POST"])
    def save_rubbers(fixture_id):
        """Save rubber player names and scores, then recalculate fixture score."""
        fixture = Fixture.query.get_or_404(fixture_id)
        err = _require_captain_for_fixture(fixture)
        if err:
            return err
        team_id = request.form.get("team_id", type=int)

        league = _effective_league(fixture)
        fmt = league.format if league else LEAGUE_FORMAT_CLEVELAND_AUTUMN
        if fmt in (LEAGUE_FORMAT_CLEVELAND_VETERANS, LEAGUE_FORMAT_NE_WINTER):
            try:
                return _save_rubbers_fixed_squad(fixture, league, team_id)
            except Exception as e:
                db.session.rollback()
                flash(f"Error saving results: {e}", "error")
                redirect_kwargs = {"fixture_id": fixture_id}
                if team_id:
                    redirect_kwargs["team_id"] = team_id
                return redirect(url_for("fixture_detail", **redirect_kwargs))

        try:
            rubber_count = int(request.form.get("rubber_count", 0))

            def _sv(key):
                v = (request.form.get(key) or "").strip()
                return int(v) if v else None

            # Determine which side is Yarm; left score in the form is always Yarm.
            both_yarm = fixture.home_team_id is not None and fixture.away_team_id is not None
            if team_id and team_id == fixture.home_team_id:
                yarm_is_home = True
            elif team_id and team_id == fixture.away_team_id:
                yarm_is_home = False
            else:
                yarm_is_home = fixture.home_team_id is not None

            yarm_side = "home" if yarm_is_home else "away"

            if both_yarm and not yarm_is_home:
                # Away team: save player pair assignments and scores bi-directionally.
                # Away pair i's cell in column n_col is stored under the HOME pair's
                # slot (home-pair-major, matching build_rubber_plan()'s numbering);
                # see autumn_away_rubber_number() for the 2-pair transpose vs the
                # larger-format rotation.
                existing_rubbers = {
                    r.rubber_number: r for r in fixture.rubbers.order_by(Rubber.rubber_number).all()
                }
                for r in existing_rubbers.values():
                    PlayerMatchResult.query.filter_by(rubber_id=r.id, side="away").delete()
                db.session.flush()
                any_scores = False
                away_played_pids = set()
                for i in range(rubber_count):
                    pid1 = request.form.get(f"rubber_{i}_home_player_1", type=int)
                    pid2 = request.form.get(f"rubber_{i}_home_player_2", type=int)
                    away_played_pids.update(p for p in [pid1, pid2] if p)
                    for n_col in range(1, rubber_count + 1):
                        rubber_number = autumn_away_rubber_number(i, n_col, rubber_count)
                        rubber = existing_rubbers.get(rubber_number)
                        if rubber is None:
                            rubber = Rubber(
                                fixture_id=fixture.id,
                                rubber_number=rubber_number,
                                rubber_type="doubles",
                            )
                            db.session.add(rubber)
                            db.session.flush()
                            existing_rubbers[rubber_number] = rubber
                        # Only update scores when the walkover-hidden field was submitted
                        # (disabled inputs are absent from the POST for locked/uncommitted rows).
                        score_submitted = (
                            request.form.get(f"rubber_{i}_opp{n_col}_walkover") is not None
                        )
                        if score_submitted:
                            any_scores = True
                            tied = request.form.get(f"rubber_{i}_opp{n_col}_tie") == "on"
                            walkover = request.form.get(f"rubber_{i}_opp{n_col}_walkover") or None
                            if walkover not in ("home", "away"):
                                walkover = None
                            if walkover:
                                tied = False
                            if tied:
                                h1, a1, h2, a2, htb, atb = None, None, None, None, None, None
                            elif walkover:
                                h1, a1 = (8, 0) if walkover == "home" else (0, 8)
                                h2 = a2 = htb = atb = None
                            else:
                                # yarm is away here, so yarm's score is the away score.
                                yarm1 = _sv(f"rubber_{i}_opp{n_col}_yarm_set1")
                                opp1 = _sv(f"rubber_{i}_opp{n_col}_opp_set1")
                                yarm2 = _sv(f"rubber_{i}_opp{n_col}_yarm_set2")
                                opp2 = _sv(f"rubber_{i}_opp{n_col}_opp_set2")
                                yarmtb = _sv(f"rubber_{i}_opp{n_col}_yarm_set3")
                                opptb = _sv(f"rubber_{i}_opp{n_col}_opp_set3")
                                h1, a1 = opp1, yarm1
                                h2, a2 = opp2, yarm2
                                htb, atb = opptb, yarmtb
                            rubber.home_set1 = h1
                            rubber.away_set1 = a1
                            rubber.home_set2 = h2
                            rubber.away_set2 = a2
                            rubber.home_set3 = htb
                            rubber.away_set3 = atb
                            rubber.set1_tie = tied
                            rubber.set1_walkover = walkover
                            if tied:
                                rubber.winner = "tie"
                            else:
                                rubber.winner = rubber_winner_from_sets(h1, a1, h2, a2, htb, atb)
                            db.session.flush()
                        yarm_won = rubber.winner is not None and rubber.winner == "away"
                        for pid in [pid1, pid2]:
                            if pid:
                                db.session.add(
                                    PlayerMatchResult(
                                        player_id=pid,
                                        rubber_id=rubber.id,
                                        side="away",
                                        won=yarm_won,
                                    )
                                )
                if any_scores:
                    fixture.home_score, fixture.away_score = score_from_rubber_results(
                        existing_rubbers.values()
                    )
                _ensure_squad_entries(fixture, away_played_pids, fixture.away_team_id)
                _ensure_team_roster(away_played_pids, fixture.away_team_id)
                _ensure_team_commitments(fixture, away_played_pids, fixture.away_team_id)
                _touch_scores_updated()
                db.session.commit()
                flash("Results saved.", "success")
                redirect_kwargs = {"fixture_id": fixture_id}
                if team_id:
                    redirect_kwargs["team_id"] = team_id
                return redirect(url_for("fixture_detail", **redirect_kwargs))

            if both_yarm:
                # Home team in dual-Yarm fixture: keep rubbers, replace only home player results.
                existing_rubbers = {
                    r.rubber_number: r for r in fixture.rubbers.order_by(Rubber.rubber_number).all()
                }
                for r in existing_rubbers.values():
                    PlayerMatchResult.query.filter_by(rubber_id=r.id, side=yarm_side).delete()
                db.session.flush()
            else:
                existing_rubbers = {}
                for r in fixture.rubbers.all():
                    db.session.delete(r)
                db.session.flush()

            yarm_played_pids = set()

            rubber_sequence = 0
            for i in range(rubber_count):
                pid1 = request.form.get(f"rubber_{i}_home_player_1", type=int)
                pid2 = request.form.get(f"rubber_{i}_home_player_2", type=int)
                yarm_played_pids.update(p for p in [pid1, pid2] if p)

                # Each column n (1..rubber_count) is a separate rubber against one
                # opposition pair — a full round-robin, not a fixed 3 rounds.
                for n in range(1, rubber_count + 1):
                    rubber_sequence += 1
                    tied = request.form.get(f"rubber_{i}_opp{n}_tie") == "on"
                    walkover = request.form.get(f"rubber_{i}_opp{n}_walkover") or None
                    if walkover not in ("home", "away"):
                        walkover = None
                    if walkover:
                        tied = False

                    if tied:
                        h1, a1, h2, a2, htb, atb = None, None, None, None, None, None
                    elif walkover:
                        h1, a1 = (8, 0) if walkover == "home" else (0, 8)
                        h2 = a2 = htb = atb = None
                    else:
                        yarm1 = _sv(f"rubber_{i}_opp{n}_yarm_set1")
                        opp1 = _sv(f"rubber_{i}_opp{n}_opp_set1")
                        yarm2 = _sv(f"rubber_{i}_opp{n}_yarm_set2")
                        opp2 = _sv(f"rubber_{i}_opp{n}_opp_set2")
                        yarmtb = _sv(f"rubber_{i}_opp{n}_yarm_set3")
                        opptb = _sv(f"rubber_{i}_opp{n}_opp_set3")
                        h1, a1 = (yarm1, opp1) if yarm_is_home else (opp1, yarm1)
                        h2, a2 = (yarm2, opp2) if yarm_is_home else (opp2, yarm2)
                        htb, atb = (yarmtb, opptb) if yarm_is_home else (opptb, yarmtb)

                    rubber = existing_rubbers.get(rubber_sequence)
                    if rubber is None:
                        rubber = Rubber(
                            fixture_id=fixture.id,
                            rubber_number=rubber_sequence,
                            rubber_type="doubles",
                        )
                        db.session.add(rubber)
                        existing_rubbers[rubber_sequence] = rubber
                    rubber.home_set1 = h1
                    rubber.away_set1 = a1
                    rubber.home_set2 = h2
                    rubber.away_set2 = a2
                    rubber.home_set3 = htb
                    rubber.away_set3 = atb
                    rubber.set1_tie = tied
                    rubber.set1_walkover = walkover

                    if tied:
                        rubber.winner = "tie"
                    else:
                        rubber.winner = rubber_winner_from_sets(h1, a1, h2, a2, htb, atb)

                    db.session.flush()

                    yarm_won = (
                        not tied
                        and rubber.winner is not None
                        and (rubber.winner == "home") == yarm_is_home
                    )
                    for pid in [pid1, pid2]:
                        if pid:
                            db.session.add(
                                PlayerMatchResult(
                                    player_id=pid,
                                    rubber_id=rubber.id,
                                    side=yarm_side,
                                    won=yarm_won,
                                )
                            )

            fixture.home_score, fixture.away_score = score_from_rubber_results(
                existing_rubbers.values()
            )
            yarm_team_id = fixture.home_team_id if yarm_is_home else fixture.away_team_id
            _ensure_squad_entries(fixture, yarm_played_pids, yarm_team_id)
            _ensure_team_roster(yarm_played_pids, yarm_team_id)
            _ensure_team_commitments(fixture, yarm_played_pids, yarm_team_id)
            _touch_scores_updated()
            db.session.commit()
            flash("Results saved.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving results: {e}", "error")
        redirect_kwargs = {"fixture_id": fixture_id}
        if team_id:
            redirect_kwargs["team_id"] = team_id
        return redirect(url_for("fixture_detail", **redirect_kwargs))

    @app.route("/fixtures/bulk-delete", methods=["POST"])
    def bulk_delete_fixtures():
        """Delete multiple fixtures by ID."""
        err = _require_admin()
        if err:
            return err
        fixture_ids = request.form.getlist("fixture_ids", type=int)
        if not fixture_ids:
            flash("No fixtures selected.", "warning")
            return redirect(url_for("list_fixtures"))
        deleted = 0
        for fid in fixture_ids:
            fixture = Fixture.query.get(fid)
            if fixture:
                db.session.delete(fixture)
                deleted += 1
        db.session.commit()
        flash(f"Deleted {deleted} fixture(s).", "success")
        return redirect(url_for("list_fixtures"))

    @app.route("/fixtures/<int:fixture_id>/clear-results", methods=["POST"])
    def clear_fixture_results(fixture_id):
        """Delete all rubber results and reset the fixture score to unplayed."""
        err = _require_admin()
        if err:
            return err
        fixture = Fixture.query.get_or_404(fixture_id)
        team_id = request.form.get("team_id", type=int)
        for rubber in fixture.rubbers:
            rubber.player_results.delete()
            rubber.home_set1 = rubber.away_set1 = None
            rubber.home_set2 = rubber.away_set2 = None
            rubber.home_set3 = rubber.away_set3 = None
            rubber.set1_tie = rubber.set2_tie = rubber.set3_tie = False
            rubber.set1_walkover = None
            rubber.winner = None
            rubber.home_player_1 = rubber.home_player_2 = None
        fixture.home_score = 0
        fixture.away_score = 0
        db.session.commit()
        redirect_kwargs = {"fixture_id": fixture_id}
        if team_id:
            redirect_kwargs["team_id"] = team_id
        return redirect(url_for("fixture_detail", **redirect_kwargs))

    @app.route("/fixtures/<int:fixture_id>/delete", methods=["POST"])
    def delete_fixture(fixture_id):
        err = _require_admin()
        if err:
            return err
        fixture = Fixture.query.get_or_404(fixture_id)
        home_team_name = fixture.home_team.name if fixture.home_team else "—"
        away_team_name = fixture.away_team.name if fixture.away_team else "—"
        fixture_date = fixture.date
        db.session.delete(fixture)
        db.session.commit()
        flash(
            f"Fixture '{home_team_name} vs {away_team_name}' on "
            f"{fixture_date.strftime('%d %b %Y')} has been deleted.",
            "success",
        )
        return redirect(url_for("list_fixtures"))

    @app.route("/fixtures")
    def list_fixtures():
        """List all fixtures grouped by calendar week."""
        sel_leagues = _selected_leagues()
        sel_ids = [lg.id for lg in sel_leagues]
        fixtures = fixtures_query_for_league(sel_ids).order_by(Fixture.date.asc()).all()
        leagues = sel_leagues if sel_leagues else League.query.order_by(League.name).all()

        fixture_groups = []
        seen_weeks: dict = {}
        filter_team_ids: set = set()
        filter_league_ids: set = set()
        fixture_season_types: dict = {}
        filter_season_types: set = set()

        for f in fixtures:
            # Group by week starting Saturday: (weekday+2)%7 gives days since last Saturday
            week_start = f.date - timedelta(days=(f.date.weekday() + 2) % 7)
            if week_start not in seen_weeks:
                group: dict = {"week_start": week_start, "fixtures": []}
                fixture_groups.append(group)
                seen_weeks[week_start] = group
            seen_weeks[week_start]["fixtures"].append(f)

            # Collect team IDs for the filter dropdown
            if f.home_team_id:
                filter_team_ids.add(f.home_team_id)
            if f.away_team_id:
                filter_team_ids.add(f.away_team_id)

            # Effective league: direct assignment or inferred from team division
            eff_league = f.league
            if not eff_league and f.home_team and f.home_team.division:
                eff_league = f.home_team.division.league
            if not eff_league and f.away_team and f.away_team.division:
                eff_league = f.away_team.division.league
            if eff_league:
                filter_league_ids.add(eff_league.id)
                if eff_league.parent_league and eff_league.parent_league.season_type:
                    season_type = eff_league.parent_league.season_type
                    fixture_season_types[f.id] = season_type
                    filter_season_types.add(season_type)

        filter_teams = (
            Team.query.filter(Team.id.in_(filter_team_ids)).order_by(Team.name).all()
            if filter_team_ids
            else []
        )
        filter_leagues = [lg for lg in leagues if lg.id in filter_league_ids]
        filter_season_badges = [
            {
                "type": value,
                "letter": SEASON_TYPE_BADGES[value],
                "label": label,
                "colour": SEASON_TYPE_COLOURS[value],
                "text_colour": _contrast_text(SEASON_TYPE_COLOURS[value]),
            }
            for value, label in SEASON_TYPE_CHOICES
            if value in filter_season_types
        ]

        today = date.today()
        today_week_start = today - timedelta(days=(today.weekday() + 2) % 7)

        # Fixtures that have at least one rubber with a score already entered
        rubber_scored_ids = {
            r[0]
            for r in db.session.query(Rubber.fixture_id)
            .filter(
                db.or_(
                    Rubber.home_set1.isnot(None),
                    Rubber.set1_tie.is_(True),
                    Rubber.set1_walkover.isnot(None),
                )
            )
            .distinct()
            .all()
        }
        # Fixtures with a match score but no rubber scores yet
        fixtures_missing_rubber_scores = {
            f.id
            for f in fixtures
            if (f.home_score or f.away_score)
            and f.id not in rubber_scored_ids
            and not f.walkover_winner
        }

        return render_template(
            "fixtures.html",
            fixtures=fixtures,
            fixture_groups=fixture_groups,
            leagues=leagues,
            filter_leagues=filter_leagues,
            filter_teams=filter_teams,
            filter_season_badges=filter_season_badges,
            fixture_season_types=fixture_season_types,
            today=today,
            today_week_start=today_week_start,
            fixtures_missing_rubber_scores=fixtures_missing_rubber_scores,
        )

    @app.route("/fixtures/bulk-assign-league", methods=["POST"])
    def bulk_assign_league():
        """Assign a league to selected fixtures and recompute their round labels."""
        err = _require_admin()
        if err:
            return err
        fixture_ids = request.form.getlist("fixture_ids", type=int)
        league_id = request.form.get("league_id", type=int)
        if not fixture_ids:
            flash("No fixtures selected.", "warning")
            return redirect(url_for("list_fixtures"))
        league_obj = League.query.get(league_id) if league_id else None
        updated = 0
        for fid in fixture_ids:
            fixture = Fixture.query.get(fid)
            if fixture:
                fixture.league_id = league_id or None
                fixture.round_label = fixture_round_label(
                    fixture.date,
                    league_obj.start_date if league_obj else None,
                    league_obj.catchup_week_start_dates if league_obj else None,
                )
                updated += 1
        db.session.commit()
        if league_obj:
            flash(f"Assigned {updated} fixture(s) to {league_obj.name}.", "success")
        else:
            flash(f"Removed league from {updated} fixture(s).", "success")
        return redirect(url_for("list_fixtures"))

    # ── API endpoints ───────────────────────────────────────────────────

    @app.route("/api/teams", methods=["GET"])
    def api_list_teams():
        teams = Team.query.order_by(Team.name).all()
        return jsonify(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "division": t.division.name if t.division else None,
                    "league": t.division.league.name if t.division and t.division.league else None,
                }
                for t in teams
            ]
        )

    @app.route("/api/players/<int:player_id>/stats", methods=["GET"])
    def api_player_stats(player_id):
        player = Player.query.get_or_404(player_id)
        results = (
            PlayerMatchResult.query.filter_by(player_id=player.id)
            .join(Rubber, PlayerMatchResult.rubber_id == Rubber.id)
            .all()
        )
        wins = sum(1 for r in results if r.won and not r.rubber.set1_walkover)
        losses = sum(
            1
            for r in results
            if not r.won and r.rubber.winner != "tie" and not r.rubber.set1_walkover
        )
        teams = player.teams.all()
        return jsonify(
            {
                "player_id": player.id,
                "name": player.name,
                "teams": [{"id": t.id, "name": t.name} for t in teams],
                "matches_played": len(results),
                "wins": wins,
                "losses": losses,
                "win_percentage": round(wins / len(results) * 100, 1) if results else 0,
            }
        )

    @app.route(
        "/api/players/<int:player_id>/eligibility/<int:team_id>/<int:league_id>", methods=["GET"]
    )
    def api_player_eligibility(player_id, team_id, league_id):
        """Check if a player is eligible to play for a team in a league."""
        result = check_player_eligibility(player_id, team_id, league_id)
        return jsonify(result)

    # ── Helpers ─────────────────────────────────────────────────────────

    def _get_or_create_team(name):
        team = Team.query.filter_by(name=name).first()
        if not team:
            team = Team(name=name)
            db.session.add(team)
            db.session.flush()
        return team

    def _find_player_by_name(name):
        """Find a player by full name, matching first/last name case-insensitively.

        Player.name is a computed property, not a column, so this can't be done via
        filter_by(name=...) (that silently matches nothing).
        """
        name = (name or "").strip()
        if not name:
            return None
        first, _, last = name.rpartition(" ")
        if not first:
            first, last = last, ""
        return Player.query.filter(
            sqlfunc.lower(Player.first_name) == first.lower(),
            sqlfunc.lower(Player.last_name) == last.lower(),
        ).first()

    def _get_or_create_player(name, team_id, allow_create=True):
        """Find or create a player and add them to the team if not already on it.

        New players may only be registered by an admin (see CLAUDE.md's "Admin-only
        routes" — player CRUD). manual_fixture_entry is also reachable by a captain
        (the pitchside confirm step), so callers on that path must pass
        allow_create=False — an unmatched name then raises instead of silently
        creating a placeholder Player record.
        """
        player = _find_player_by_name(name)
        if not player:
            if not allow_create:
                raise ValueError(
                    f"Player '{name}' is not registered yet — ask an admin to add "
                    "them via Players before confirming this scoresheet."
                )
            name = (name or "").strip()
            first, _, last = name.rpartition(" ")
            if not first:
                first, last = last, ""
            player = Player(
                first_name=first,
                last_name=last,
                gender="U",
                membership_status="inactive",
                interest_team_play="no",
            )
            db.session.add(player)
            db.session.flush()
        # Ensure player is on the team
        team = Team.query.get(team_id)
        if team and team not in player.teams:
            player.teams.append(team)
        return player

    @app.route("/admin/users")
    def admin_users():
        """List Firebase users with roles and allow adding admins or captains."""
        err = _require_admin()
        if err:
            return err
        if not _firebase_project_id:
            flash("Firebase is not configured on this instance.", "error")
            return redirect(url_for("index"))
        captain_records = {c.uid: c for c in CaptainUser.query.all()}
        players = Player.query.order_by(Player.last_name, Player.first_name).all()
        users = []
        try:
            for u in firebase_auth.list_users().iterate_all():
                ts = u.user_metadata.creation_timestamp
                created = (
                    datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%d %b %Y")
                    if ts
                    else ""
                )
                captain = captain_records.get(u.uid)
                pitchside_admin = captain_records.get(_pitchside_admin_uid(u.uid))
                users.append(
                    {
                        "uid": u.uid,
                        "email": u.email or "",
                        "disabled": u.disabled,
                        "created": created,
                        "role": "captain" if captain else "admin",
                        "player": captain.player if captain else None,
                        "pitchside_player": pitchside_admin.player if pitchside_admin else None,
                    }
                )
            users.sort(key=lambda u: u["email"])
        except Exception as e:
            flash(
                f"Could not list users: {e}. "
                "Locally, run 'gcloud auth application-default login' first.",
                "error",
            )
        return render_template("admin_users.html", users=users, players=players)

    def _delete_other_captain_rows_for_email(email, keep_uid):
        """Delete any CaptainUser row sharing `email` other than `keep_uid`.

        Call this right before adding a new CaptainUser row for an email, so a
        stale row from a deleted-and-recreated Firebase account (see
        `_cleanup_duplicate_captain_user_emails`) can never coexist with the new
        one — keeps `pitchside_login`'s email lookup unambiguous going forward.
        """
        CaptainUser.query.filter(
            sqlfunc.lower(CaptainUser.email) == email.lower(), CaptainUser.uid != keep_uid
        ).delete(synchronize_session=False)

    @app.route("/admin/users", methods=["POST"])
    def admin_add_user():
        """Create a Firebase user as an admin or captain."""
        err = _require_admin()
        if err:
            return err
        if not _firebase_project_id:
            flash("Firebase is not configured on this instance.", "error")
            return redirect(url_for("index"))
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "admin")
        player_id = request.form.get("player_id", type=int)
        if not email:
            flash("Email is required.", "error")
            return redirect(url_for("admin_users"))
        try:
            new_user = firebase_auth.create_user(email=email)
        except firebase_auth.EmailAlreadyExistsError:
            flash(f"A user with email {email} already exists in Firebase.", "warning")
            return redirect(url_for("admin_users"))
        except Exception as e:
            flash(f"Could not create user: {e}", "error")
            return redirect(url_for("admin_users"))
        if role == "captain":
            _delete_other_captain_rows_for_email(email, new_user.uid)
            db.session.add(CaptainUser(uid=new_user.uid, email=email, player_id=player_id or None))
            db.session.commit()
            flash(f"Captain account created for {email}.", "success")
        else:
            if player_id:
                synthetic_uid = _pitchside_admin_uid(new_user.uid)
                _delete_other_captain_rows_for_email(email, synthetic_uid)
                db.session.add(
                    CaptainUser(
                        uid=synthetic_uid,
                        email=email,
                        player_id=player_id,
                        sees_all_fixtures=True,
                    )
                )
                db.session.commit()
            flash(f"Admin account created for {email}.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<uid>/set-role", methods=["POST"])
    def admin_set_user_role(uid):
        """Change a user's role between admin and captain."""
        err = _require_admin()
        if err:
            return err
        if uid == session.get("user_uid"):
            flash("You cannot change your own role.", "error")
            return redirect(url_for("admin_users"))
        if uid == _active_session.get("uid") and uid != session.get("user_uid"):
            flash("Cannot change the role of a user who is currently logged in.", "error")
            return redirect(url_for("admin_users"))
        new_role = request.form.get("role")
        player_id = request.form.get("player_id", type=int)
        captain = CaptainUser.query.get(uid)
        if new_role == "admin" and captain:
            db.session.delete(captain)
            db.session.commit()
            flash("User changed to Admin.", "success")
        elif new_role == "captain" and not captain:
            try:
                fb_user = firebase_auth.get_user(uid)
                email = fb_user.email or uid
            except Exception:
                email = uid
            _delete_other_captain_rows_for_email(email, uid)
            db.session.add(CaptainUser(uid=uid, email=email, player_id=player_id or None))
            db.session.commit()
            flash("User changed to Captain.", "success")
        elif new_role == "captain" and captain and player_id:
            captain.player_id = player_id
            db.session.commit()
            flash("Captain's linked player updated.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<uid>/player", methods=["POST"])
    def admin_edit_user_player(uid):
        """Link or unlink a player for an existing user, without changing their role.

        For a captain (or viewer), this updates their CaptainUser.player_id directly.
        For an admin, there's no CaptainUser row keyed by their real uid — instead this
        creates/updates/removes the synthetic pitchside-only row (see
        `_pitchside_admin_uid`) that lets them use the pitchside match-day login.
        """
        err = _require_admin()
        if err:
            return err
        player_id = request.form.get("player_id", type=int)

        captain = CaptainUser.query.get(uid)
        if captain:
            captain.player_id = player_id or None
            db.session.commit()
            flash("Linked player updated.", "success")
            return redirect(url_for("admin_users"))

        synthetic_uid = _pitchside_admin_uid(uid)
        pitchside_row = CaptainUser.query.get(synthetic_uid)
        if player_id:
            if pitchside_row:
                pitchside_row.player_id = player_id
            else:
                try:
                    fb_user = firebase_auth.get_user(uid)
                    email = fb_user.email or uid
                except Exception:
                    email = uid
                _delete_other_captain_rows_for_email(email, synthetic_uid)
                db.session.add(
                    CaptainUser(
                        uid=synthetic_uid,
                        email=email,
                        player_id=player_id,
                        sees_all_fixtures=True,
                    )
                )
            db.session.commit()
            flash(
                "Linked player updated — this admin can now sign in at the "
                "pitchside match-day login.",
                "success",
            )
        elif pitchside_row:
            db.session.delete(pitchside_row)
            db.session.commit()
            flash("Linked player removed.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<uid>/delete", methods=["POST"])
    def admin_delete_user(uid):
        """Delete a Firebase user and any associated captain record."""
        err = _require_admin()
        if err:
            return err
        if not _firebase_project_id:
            flash("Firebase is not configured on this instance.", "error")
            return redirect(url_for("index"))
        if uid == session.get("user_uid"):
            flash("You cannot delete your own account.", "error")
            return redirect(url_for("admin_users"))
        captain = CaptainUser.query.get(uid)
        if captain:
            db.session.delete(captain)
        pitchside_admin_row = CaptainUser.query.get(_pitchside_admin_uid(uid))
        if pitchside_admin_row:
            db.session.delete(pitchside_admin_row)
        if captain or pitchside_admin_row:
            db.session.commit()
        try:
            firebase_auth.delete_user(uid)
            flash("User removed.", "success")
        except Exception as e:
            flash(f"Could not delete user: {e}", "error")
        return redirect(url_for("admin_users"))

    @app.route("/admin/export-db", methods=["POST"])
    def export_db():
        """Emergency export of the live SQLite database. Requires authentication."""
        err = _require_admin()
        if err:
            return err
        db_path = os.path.join(app.instance_path, "tennis.db")
        if not os.path.exists(db_path):
            flash("Database file not found on this instance.", "error")
            return redirect(url_for("index"))
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return send_file(
            db_path,
            as_attachment=True,
            download_name=f"tennis_backup_{timestamp}.db",
            mimetype="application/octet-stream",
        )

    @app.route("/admin/captains")
    def admin_captains():
        """Redirect to the unified user access management page."""
        return redirect(url_for("admin_users"))

    @app.route("/admin/captains", methods=["POST"])
    def admin_add_captain():
        """Redirect to the unified user access management page."""
        return redirect(url_for("admin_users"))

    @app.route("/admin/captains/<uid>/delete", methods=["POST"])
    def admin_delete_captain(uid):
        """Remove a captain login record (demotes to admin without deleting Firebase account)."""
        err = _require_admin()
        if err:
            return err
        if uid == _active_session.get("uid"):
            flash("Cannot demote a user who is currently logged in.", "error")
            return redirect(url_for("admin_users"))
        captain = CaptainUser.query.get_or_404(uid)
        db.session.delete(captain)
        db.session.commit()
        flash(f"Captain login for {captain.email} removed.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/settings")
    def admin_settings():
        """Admin settings page for feature toggles."""
        err = _require_admin()
        if err:
            return err
        enable_activity = AppSetting.query.filter_by(key="enable_activity_feature").first()
        enable_activity_feature = enable_activity.value == "1" if enable_activity else False
        return render_template(
            "admin_settings.html",
            enable_activity_feature=enable_activity_feature,
            pitchside_window_days=_pitchside_window_days(),
            pitchside_window_days_min=PITCHSIDE_WINDOW_DAYS_MIN,
            pitchside_window_days_max=PITCHSIDE_WINDOW_DAYS_MAX,
        )

    @app.route("/admin/settings", methods=["POST"])
    def save_admin_settings():
        """Save admin settings."""
        err = _require_admin()
        if err:
            return err
        pitchside_days_raw = request.form.get("pitchside_window_days", "").strip()
        try:
            pitchside_days = int(pitchside_days_raw)
        except ValueError:
            pitchside_days = None
        if pitchside_days is None or not (
            PITCHSIDE_WINDOW_DAYS_MIN <= pitchside_days <= PITCHSIDE_WINDOW_DAYS_MAX
        ):
            flash(
                "Match Day window must be a whole number of days between "
                f"{PITCHSIDE_WINDOW_DAYS_MIN} and {PITCHSIDE_WINDOW_DAYS_MAX}.",
                "error",
            )
            return redirect(url_for("admin_settings"))

        enable_activity = request.form.get("enable_activity_feature") == "1"
        setting = AppSetting.query.filter_by(key="enable_activity_feature").first()
        if setting:
            setting.value = "1" if enable_activity else "0"
        else:
            setting = AppSetting(
                key="enable_activity_feature", value="1" if enable_activity else "0"
            )
            db.session.add(setting)

        pitchside_setting = AppSetting.query.filter_by(key="pitchside_window_days").first()
        if pitchside_setting:
            pitchside_setting.value = str(pitchside_days)
        else:
            db.session.add(AppSetting(key="pitchside_window_days", value=str(pitchside_days)))

        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin_settings"))

    # ── Pitchside captain login (match-day scoresheet) ─────────────────────
    #
    # A completely separate, lightweight login for captains at a match, keyed off
    # session['pitchside_player_id'] / session['pitchside_fixture_id']. It never
    # touches user_uid/session_nonce, so it is entirely independent of the single
    # active session lock above — any number of captains may be logged in pitchside
    # concurrently, regardless of who (if anyone) is logged in normally elsewhere.

    def _pitchside_admin_uid(real_uid):
        """Synthetic CaptainUser.uid for an admin's pitchside-only lookup row.

        Deliberately not the admin's real Firebase uid, so CaptainUser.query.get()
        at normal sign-in never finds it and their role stays 'admin'.
        """
        return f"pitchside-admin-{real_uid}"

    def _pitchside_captained_team_ids(player_id):
        """Return the set of team IDs the given player captains this season."""
        return {
            c.team_id for c in TeamCaptain.query.filter_by(player_id=player_id, season="2026").all()
        }

    def _pitchside_context():
        """Return the active pitchside player/fixture context, or None if unauthorized."""
        player_id = session.get("pitchside_player_id")
        fixture_id = session.get("pitchside_fixture_id")
        if not player_id or not fixture_id:
            return None
        player = Player.query.get(player_id)
        fixture = Fixture.query.get(fixture_id)
        if not player or not fixture:
            return None

        if session.get("pitchside_sees_all"):
            side = session.get("pitchside_chosen_side")
            if side not in ("home", "away"):
                side = "home" if fixture.home_team_id else "away"
            if side == "home":
                my_team, opp_team = fixture.home_team, fixture.away_team
            else:
                my_team, opp_team = fixture.away_team, fixture.home_team
            return {
                "player": player,
                "fixture": fixture,
                "my_team": my_team,
                "opp_team": opp_team,
                "my_side": side,
            }

        captained_ids = _pitchside_captained_team_ids(player_id)
        if fixture.home_team_id in captained_ids:
            my_team, opp_team, my_side = fixture.home_team, fixture.away_team, "home"
        elif fixture.away_team_id in captained_ids:
            my_team, opp_team, my_side = fixture.away_team, fixture.home_team, "away"
        else:
            return None
        return {
            "player": player,
            "fixture": fixture,
            "my_team": my_team,
            "opp_team": opp_team,
            "my_side": my_side,
        }

    def _pitchside_live_uid_exists(real_uid):
        """Return True if `real_uid` is still a non-disabled Firebase account.

        Assume live when Firebase isn't configured (local dev) — there's no
        authority to check against, so every candidate is treated as live.
        """
        if not _firebase_project_id:
            return True
        try:
            return not firebase_auth.get_user(real_uid).disabled
        except Exception:
            return False

    def _pitchside_resolve_captain_record(email):
        """Return the one CaptainUser row for `email` that matches a live Firebase account.

        A Firebase account deleted and later recreated with the same email (e.g.
        via the Firebase console rather than `admin_delete_user`, which cleans up
        the CaptainUser row) leaves its old CaptainUser row behind — orphaned, but
        still matching on email. `CaptainUser.query.filter(email==...).first()`
        can't distinguish an orphan from the current row, so with 2+ rows sharing
        an email it can nondeterministically pick a stale one (wrong
        `sees_all_fixtures`, or even a stale row with no `player_id` at all,
        which fails the login outright). Prefer whichever candidate's
        (de-synthesized) uid is still a live Firebase account.
        """
        candidates = (
            CaptainUser.query.filter(sqlfunc.lower(CaptainUser.email) == email).all()
            if email
            else []
        )
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        for candidate in candidates:
            real_uid = (
                candidate.uid[len("pitchside-admin-") :]
                if candidate.uid.startswith("pitchside-admin-")
                else candidate.uid
            )
            if _pitchside_live_uid_exists(real_uid):
                return candidate
        return candidates[0]

    @app.route("/pitchside/login", methods=["GET", "POST"])
    def pitchside_login():
        """Mobile-friendly login for captains at a match: email + last-4 LTA digits.

        The same login also covers admins — an admin who has a linked Player
        profile gets a CaptainUser row created for them (see `admin_add_user`)
        flagged `sees_all_fixtures=True`, so they sign in here the same way but
        see every fixture today rather than only ones for a team they captain.
        """
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            last4 = (request.form.get("lta_last4") or "").strip()
            captain_record = _pitchside_resolve_captain_record(email)
            player = (
                Player.query.get(captain_record.player_id)
                if captain_record and captain_record.player_id
                else None
            )
            lta = (player.lta_number or "").strip() if player else ""
            if not player or not last4 or len(lta) < 4 or lta[-4:].lower() != last4.lower():
                flash("Email or LTA number didn't match. Please try again.", "error")
                return redirect(url_for("pitchside_login"))

            session["pitchside_player_id"] = player.id
            session["pitchside_sees_all"] = bool(captain_record.sees_all_fixtures)
            session.pop("pitchside_fixture_id", None)
            session.pop("pitchside_chosen_side", None)
            return redirect(url_for("pitchside_fixtures"))

        return render_template("pitchside_login.html")

    @app.route("/pitchside/logout")
    def pitchside_logout():
        """Clear only the pitchside session keys."""
        session.pop("pitchside_player_id", None)
        session.pop("pitchside_fixture_id", None)
        session.pop("pitchside_sees_all", None)
        session.pop("pitchside_chosen_side", None)
        return redirect(url_for("pitchside_login"))

    def _pitchside_window_days():
        """Return the admin-configured pitchside window length in days.

        Falls back to PITCHSIDE_WINDOW_DAYS_DEFAULT if unset or invalid.
        """
        setting = AppSetting.query.filter_by(key="pitchside_window_days").first()
        if setting and setting.value:
            try:
                days = int(setting.value)
            except ValueError:
                days = None
            if days and PITCHSIDE_WINDOW_DAYS_MIN <= days <= PITCHSIDE_WINDOW_DAYS_MAX:
                return days
        return PITCHSIDE_WINDOW_DAYS_DEFAULT

    def _pitchside_window():
        """Return (start, end) inclusive date range pitchside logins can access.

        Today plus the admin-configured number of days ahead (see
        _pitchside_window_days) — lets a captain open an upcoming fixture well
        in advance (e.g. to submit Yarm's pairs ahead of the match) as well as
        anything happening today. This bounds both the sees-all admin listing
        and the captain-scoped listing (see pitchside_fixtures) equally —
        only which fixtures within the window are visible differs by role.
        """
        today = date.today()
        return today, today + timedelta(days=_pitchside_window_days())

    @app.route("/pitchside/fixtures")
    def pitchside_fixtures():
        """List fixtures accessible today or up to the admin-configured window ahead.

        Sees-all logins (admins) see every such fixture; ordinary captains see
        only ones involving a team they captain.
        """
        player_id = session.get("pitchside_player_id")
        if not player_id:
            return redirect(url_for("pitchside_login"))
        player = Player.query.get(player_id)
        if not player:
            return redirect(url_for("pitchside_login"))

        today, window_end = _pitchside_window()
        sees_all = bool(session.get("pitchside_sees_all"))
        if sees_all:
            fixtures = (
                Fixture.query.filter(Fixture.date >= today, Fixture.date <= window_end)
                .order_by(Fixture.date, Fixture.id)
                .all()
            )
        else:
            captained_ids = _pitchside_captained_team_ids(player_id)
            fixtures = (
                Fixture.query.filter(
                    Fixture.date >= today,
                    Fixture.date <= window_end,
                    db.or_(
                        Fixture.home_team_id.in_(captained_ids),
                        Fixture.away_team_id.in_(captained_ids),
                    ),
                )
                .order_by(Fixture.date, Fixture.id)
                .all()
                if captained_ids
                else []
            )
        return render_template(
            "pitchside_fixtures.html",
            player=player,
            sees_all=sees_all,
            fixtures=fixtures,
            today=today,
            tomorrow=today + timedelta(days=1),
            window_days=_pitchside_window_days(),
        )

    @app.route("/pitchside/select/<int:fixture_id>")
    def pitchside_select_fixture(fixture_id):
        """Set the active fixture (and, for sees-all logins, which side) then go to entry."""
        player_id = session.get("pitchside_player_id")
        if not player_id:
            return redirect(url_for("pitchside_login"))
        fixture = Fixture.query.get_or_404(fixture_id)
        today, window_end = _pitchside_window()
        if not (today <= fixture.date <= window_end):
            flash(f"That fixture isn't within the next {_pitchside_window_days()} days.", "error")
            return redirect(url_for("pitchside_fixtures"))

        if session.get("pitchside_sees_all"):
            side = request.args.get("side")
            if side not in ("home", "away"):
                side = "home" if fixture.home_team_id else "away"
            session["pitchside_fixture_id"] = fixture.id
            session["pitchside_chosen_side"] = side
            return redirect(url_for("pitchside_entry"))

        captained_ids = _pitchside_captained_team_ids(player_id)
        if fixture.home_team_id not in captained_ids and fixture.away_team_id not in captained_ids:
            flash("You don't captain a team in that fixture.", "error")
            return redirect(url_for("pitchside_fixtures"))
        session["pitchside_fixture_id"] = fixture.id
        return redirect(url_for("pitchside_entry"))

    @app.route("/pitchside/entry")
    def pitchside_entry():
        """Render the pitchside scoresheet entry form for the fixture in session.

        cleveland_autumn fixtures (the default) follow the club's standard
        doubles format: a fixed number of pairs per side (League.pairs_per_round)
        playing a full round-robin against the opposition's pairs — pairs_per_round
        rounds, pairs_per_round^2 rubbers (9 for Summer's 3 pairs, 4 for Autumn's
        2). The two fixed-squad formats
        (cleveland_veterans, ne_winter) instead use a flat, non-rotating rubber
        plan built from build_rubber_plan() — see fixture_detail's equivalent
        squad-picker + rubber-grid partial for the same shape. Player names on
        both sides are free text (matched by name at confirm time, same as
        manual_fixture_entry); "my team" gets predictive suggestions from that
        team's own active roster only (unlike manual_fixture_entry's confirm
        step, which still suggests club-wide so a sub can be entered there).
        """
        ctx = _pitchside_context()
        if not ctx:
            return redirect(url_for("pitchside_fixtures"))
        draft = MatchDayDraft.query.filter_by(fixture_id=ctx["fixture"].id).first()
        draft_rubbers = []
        if draft and draft.data:
            try:
                draft_rubbers = json.loads(draft.data).get("rubbers", [])
            except ValueError:
                draft_rubbers = []
        league = _effective_league(ctx["fixture"])
        league_format = league.format if league else LEAGUE_FORMAT_CLEVELAND_AUTUMN
        n_pairs = league.pairs_per_round if (league and league.pairs_per_round) else 3
        # A full round-robin: every pair plays every opposition pair exactly
        # once, so the round count always equals n_pairs (not a fixed 3).
        n_rounds = n_pairs
        rubber_plan_json = json.dumps(
            [
                {
                    "rubber_number": slot.rubber_number,
                    "label": slot.label,
                    "sets": slot.sets,
                    "home_slots": list(slot.home_slots) if slot.home_slots else None,
                    "away_slots": list(slot.away_slots) if slot.away_slots else None,
                }
                for slot in build_rubber_plan(league)
            ]
        )
        my_team = ctx["my_team"]
        all_player_names = (
            [
                p.name
                for p in my_team.players.filter_by(membership_status="active")
                .order_by(Player.first_name, Player.last_name)
                .all()
            ]
            if my_team
            else []
        )
        autumn_parent = league.parent_league if league else None
        autumn_games_per_set = (
            autumn_parent.games_per_set if autumn_parent and autumn_parent.games_per_set else 6
        )
        return render_template(
            "pitchside_entry.html",
            ctx=ctx,
            league=league,
            n_pairs=n_pairs,
            autumn_games_per_set=autumn_games_per_set,
            n_rounds=n_rounds,
            league_format=league_format,
            rubber_plan_json=rubber_plan_json,
            all_player_names_json=json.dumps(all_player_names),
            draft=draft,
            draft_rubbers_json=json.dumps(draft_rubbers),
        )

    def _pitchside_save_draft(ctx, mark_submitted):
        payload = request.get_json(silent=True) or {}
        rubbers = payload.get("rubbers", [])
        now = datetime.utcnow()
        draft = MatchDayDraft.query.filter_by(fixture_id=ctx["fixture"].id).first()
        if not draft:
            draft = MatchDayDraft(
                fixture_id=ctx["fixture"].id,
                started_by_player_id=ctx["player"].id,
                created_at=now,
            )
            db.session.add(draft)
        draft.data = json.dumps({"rubbers": rubbers})
        draft.last_saved_at = now
        if mark_submitted:
            draft.status = "submitted"
            draft.submitted_at = now
            draft.submitted_by_player_id = ctx["player"].id
        elif draft.status != "submitted":
            draft.status = "draft"
        db.session.commit()
        return draft

    @app.route("/pitchside/entry/save", methods=["POST"])
    def pitchside_save():
        """AJAX: save progress on the current draft without marking it submitted."""
        ctx = _pitchside_context()
        if not ctx:
            return jsonify({"error": "not authorized"}), 403
        draft = _pitchside_save_draft(ctx, mark_submitted=False)
        return jsonify(
            {"ok": True, "status": draft.status, "saved_at": draft.last_saved_at.isoformat()}
        )

    @app.route("/pitchside/entry/submit", methods=["POST"])
    def pitchside_submit():
        """AJAX: save and mark the draft as submitted (final), ready for confirmation."""
        ctx = _pitchside_context()
        if not ctx:
            return jsonify({"error": "not authorized"}), 403
        draft = _pitchside_save_draft(ctx, mark_submitted=True)
        return jsonify({"ok": True, "status": draft.status})

    return app
