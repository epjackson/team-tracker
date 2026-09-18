"""Database models for the tennis team recording app."""

import re
from datetime import date, datetime, timedelta

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# ── Association table: Player ↔ Team (many-to-many) ──────────────────────

player_teams = db.Table(
    "player_teams",
    db.Column("player_id", db.Integer, db.ForeignKey("players.id"), primary_key=True),
    db.Column("team_id", db.Integer, db.ForeignKey("teams.id"), primary_key=True),
)


# ── League & Division ────────────────────────────────────────────────────

LEAGUE_FORMAT_CLEVELAND_AUTUMN = "cleveland_autumn"
LEAGUE_FORMAT_CLEVELAND_VETERANS = "cleveland_veterans"
LEAGUE_FORMAT_NE_WINTER = "ne_winter"

LEAGUE_FORMAT_CHOICES = [
    (LEAGUE_FORMAT_CLEVELAND_AUTUMN, "Cleveland Autumn (pairs rotation)"),
    (LEAGUE_FORMAT_CLEVELAND_VETERANS, "Cleveland Veterans (6 fixed rubbers)"),
    (LEAGUE_FORMAT_NE_WINTER, "North-East Winter (4 fixed rubbers, 2 sets, set-points)"),
]
LEAGUE_FORMAT_LABELS = dict(LEAGUE_FORMAT_CHOICES)

# Season-type badges (S/A/V/W) shown in the nav and on the Home page cards. A
# ParentLeague is tagged with one of these so its Leagues can be grouped into
# a single switchable "season" regardless of how many gender editions it has.
SEASON_TYPE_SUMMER = "summer"
SEASON_TYPE_AUTUMN = "autumn"
SEASON_TYPE_VETERANS = "veterans"
SEASON_TYPE_WINTER = "winter"

SEASON_TYPE_CHOICES = [
    (SEASON_TYPE_SUMMER, "Summer"),
    (SEASON_TYPE_AUTUMN, "Autumn"),
    (SEASON_TYPE_VETERANS, "Veterans"),
    (SEASON_TYPE_WINTER, "Winter"),
]
SEASON_TYPE_LABELS = dict(SEASON_TYPE_CHOICES)
SEASON_TYPE_BADGES = {
    SEASON_TYPE_SUMMER: "S",
    SEASON_TYPE_AUTUMN: "A",
    SEASON_TYPE_VETERANS: "V",
    SEASON_TYPE_WINTER: "W",
}

# Fixed badge colours, independent of any ParentLeague.colour (which stays admin-editable
# for the Leagues page's own grouping display). Chosen for even hue spacing against each
# other and against the app's forest-green theme (#0f4c3a/#1a6b50/#90ee90): amber, rust,
# plum, and steel-blue each sit 45°+ apart on the wheel and away from green's ~150° hue.
SEASON_TYPE_COLOURS = {
    SEASON_TYPE_SUMMER: "#e0a526",  # warm amber/gold
    SEASON_TYPE_AUTUMN: "#c1440e",  # burnt rust/orange
    SEASON_TYPE_VETERANS: "#6a4c93",  # muted plum/violet
    SEASON_TYPE_WINTER: "#2166ac",  # frosty steel blue
}

# Fixed badge colours for the three doubles disciplines, keyed by the same short
# label ("Men's"/"Women's"/"Mixed") fixtures.html derives from a league's name.
# Deliberately independent of ParentLeague.colour (the season swatch, admin-editable
# elsewhere) so a discipline badge is never accidentally the same colour as — or as
# easily mis-set as — the season badge it's displayed next to on the Fixtures page.
DISCIPLINE_COLOURS = {
    "Men's": "#1971c2",  # clear blue
    "Women's": "#c2255c",  # rose pink
    "Mixed": "#0c8599",  # teal
}


class ParentLeague(db.Model):
    """A format+brand definition shared by one or more Leagues.

    Locks in the doubles format (pairs per round, sets, games, tie-break) for
    every League that belongs to it, plus a display colour for UI badges.
    """

    __tablename__ = "parent_leagues"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    colour = db.Column(db.String(7), nullable=False, default="#2e7d32")
    description = db.Column(db.Text, nullable=True)

    pairs_per_round = db.Column(
        db.Integer, nullable=False, comment="Number of doubles pairs per round"
    )
    sets_per_rubber = db.Column(db.Integer, nullable=False, default=1, comment="Descriptive only")
    games_per_set = db.Column(db.Integer, nullable=False, default=8, comment="Descriptive only")
    tiebreak_at_games = db.Column(db.Integer, nullable=False, default=8, comment="Descriptive only")
    format = db.Column(
        db.String(30),
        nullable=False,
        default=LEAGUE_FORMAT_CLEVELAND_AUTUMN,
        comment="Rubber structure/scoring format — see build_rubber_plan()",
    )
    season_type = db.Column(
        db.String(20),
        nullable=True,
        comment="Which nav badge (summer/autumn/veterans/winter) this belongs to, if any",
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    leagues = db.relationship("League", backref="parent_league")


class League(db.Model):
    """A tennis league (e.g. 'Summer League', 'Winter League').

    Each league can have its own player eligibility restrictions.
    """

    __tablename__ = "leagues"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    start_date = db.Column(db.Date, nullable=True, comment="Saturday that starts Round 1")
    round5_start_date = db.Column(
        db.Date,
        nullable=True,
        comment="Saturday that Round 5 begins (after the first catch-up week)",
    )
    round9_start_date = db.Column(
        db.Date,
        nullable=True,
        comment="Saturday that Round 9 begins (after the second catch-up week)",
    )
    pairs_per_round = db.Column(
        db.Integer, nullable=True, comment="Number of doubles pairs per round"
    )
    format = db.Column(
        db.String(30),
        nullable=False,
        default=LEAGUE_FORMAT_CLEVELAND_AUTUMN,
        comment="Rubber structure/scoring format — see build_rubber_plan()",
    )
    parent_league_id = db.Column(db.Integer, db.ForeignKey("parent_leagues.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    divisions = db.relationship(
        "Division", backref="league", lazy="dynamic", cascade="all, delete-orphan"
    )
    restrictions = db.relationship(
        "LeagueRestriction", backref="league", lazy="dynamic", cascade="all, delete-orphan"
    )
    catchup_weeks = db.relationship(
        "LeagueCatchupWeek",
        backref="league",
        lazy="dynamic",
        cascade="all, delete-orphan",
        order_by="LeagueCatchupWeek.start_date",
    )

    @property
    def catchup_week_start_dates(self):
        """Return this league's catch-up week Saturdays, sorted."""
        return [cw.start_date for cw in self.catchup_weeks]

    def delete_league(self):
        """Delete the league and cascade deletions to associated divisions, teams, etc."""
        db.session.delete(self)
        db.session.commit()
        return True


class LeagueCatchupWeek(db.Model):
    """A single catch-up week (no rounds played) within a league's season."""

    __tablename__ = "league_catchup_weeks"

    id = db.Column(db.Integer, primary_key=True)
    league_id = db.Column(db.Integer, db.ForeignKey("leagues.id"), nullable=False)
    start_date = db.Column(
        db.Date, nullable=False, comment="Saturday that begins the catch-up week"
    )


class Division(db.Model):
    """A division within a league (e.g. 'Division 1', 'Division 2').

    Teams belong to a division. A club can have multiple teams
    in different divisions of the same league.
    """

    __tablename__ = "divisions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    league_id = db.Column(db.Integer, db.ForeignKey("leagues.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teams = db.relationship("Team", backref="division", lazy="dynamic")

    __table_args__ = (db.UniqueConstraint("name", "league_id", name="uq_division_league"),)

    def delete_division(self):
        """Delete the division and cascade deletions to associated teams."""
        db.session.delete(self)
        db.session.commit()
        return True


class PlayerTeamCommitment(db.Model):
    """Tracks players committed to specific teams within a league.

    When a player reaches the team_commitment_threshold for a team in a league,
    they become committed to that team for the rest of the season and cannot
    play for other teams in that league.
    """

    __tablename__ = "player_team_commitments"

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    league_id = db.Column(db.Integer, db.ForeignKey("leagues.id"), nullable=False)
    committed_at = db.Column(
        db.DateTime, default=datetime.utcnow, comment="When the commitment was recorded"
    )
    season = db.Column(
        db.String(20), nullable=False, default="2026", comment="Season identifier (e.g. '2026')"
    )

    player = db.relationship("Player", backref="team_commitments")
    team = db.relationship("Team", backref="player_commitments")
    league = db.relationship("League", backref="player_commitments")

    __table_args__ = (
        db.UniqueConstraint(
            "player_id", "league_id", "season", name="uq_player_league_season_commitment"
        ),
    )

    def delete_player_team_commitment(self):
        """Delete the player commitment record."""
        db.session.delete(self)
        db.session.commit()
        return True


class LeagueRestriction(db.Model):
    """Player team commitment restriction for a league.

    Defines when a player becomes tied to a team. Once a player plays
    the specified number of fixtures for a team in a league, they are
    committed to that team for the rest of the season.
    """

    __tablename__ = "league_restrictions"

    id = db.Column(db.Integer, primary_key=True)
    league_id = db.Column(db.Integer, db.ForeignKey("leagues.id"), nullable=False)
    max_matches_per_team = db.Column(
        db.Integer, nullable=False, default=0, comment="Deprecated: no longer used"
    )
    early_season_weeks = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        comment="Number of weeks at season start where a player may only play one match per week."
        " 0 = disabled",
    )
    team_commitment_threshold = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        comment="After this many fixtures, player is tied to the team for the season. 0 = disabled",
    )
    description = db.Column(
        db.Text, nullable=True, comment="Human-readable description of the restriction"
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        """Return string representation of LeagueRestriction."""
        return (
            f"<LeagueRestriction league={self.league_id} "
            f"threshold={self.team_commitment_threshold}>"
        )


# ── Team & Player ────────────────────────────────────────────────────────


class Team(db.Model):
    """A tennis team within a division.

    A club can have multiple teams in different divisions.
    For example, 'Riverside A' in Division 1 and 'Riverside B' in Division 2.
    """

    __tablename__ = "teams"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    division_id = db.Column(db.Integer, db.ForeignKey("divisions.id"), nullable=True)
    match_fees_enabled = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    players = db.relationship("Player", secondary=player_teams, backref="teams", lazy="dynamic")
    home_fixtures = db.relationship(
        "Fixture", foreign_keys="Fixture.home_team_id", backref="home_team", lazy="dynamic"
    )
    away_fixtures = db.relationship(
        "Fixture", foreign_keys="Fixture.away_team_id", backref="away_team", lazy="dynamic"
    )


class Player(db.Model):
    """An individual tennis player.

    Players can belong to multiple teams (e.g. a player might play for
    their club's A team in one league and B team in another).
    League restrictions govern how many matches they can play per team.
    """

    __tablename__ = "players"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(60), nullable=False)
    last_name = db.Column(db.String(60), nullable=False)
    gender = db.Column(db.String(1), nullable=False)  # 'M' or 'F'
    membership_status = db.Column(db.String(10), nullable=False)  # 'active' or 'inactive'
    interest_team_play = db.Column(db.String(3), nullable=False)  # 'yes' or 'no'
    lta_number = db.Column(db.String(30), nullable=True)
    contact_telephone = db.Column(db.String(30), nullable=True)
    miscellaneous = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    match_results = db.relationship("PlayerMatchResult", backref="player", lazy="dynamic")

    def __repr__(self):
        """Return string representation of Player."""
        return f"<Player {self.first_name} {self.last_name}>"

    @property
    def name(self):
        """Return full name."""
        return f"{self.first_name} {self.last_name}"

    def appearances_for_team_in_league(self, team_id, league_id):
        """Return number of fixtures this player has appeared in for given team and league."""
        return (
            PlayerMatchResult.query.join(Rubber)
            .join(Fixture)
            .filter(
                PlayerMatchResult.player_id == self.id,
                Fixture.league_id == league_id,
                (
                    Rubber.player_results.any(
                        PlayerMatchResult.player_id == self.id,
                        PlayerMatchResult.side == db.literal("home"),
                    )
                    if False
                    else True
                ),
            )
            .count()
        )


# ── Fixture & Rubber ────────────────────────────────────────────────────


class Fixture(db.Model):
    """A fixture (match day) between two teams, containing multiple rubbers."""

    __tablename__ = "fixtures"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    league_id = db.Column(db.Integer, db.ForeignKey("leagues.id"), nullable=True)
    home_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=True)
    away_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=True)
    home_team_name = db.Column(db.String(200), nullable=True)
    away_team_name = db.Column(db.String(200), nullable=True)
    home_score = db.Column(db.Integer, default=0)
    away_score = db.Column(db.Integer, default=0)
    walkover_winner = db.Column(db.String(10), nullable=True)  # 'home', 'away', or None
    round_label = db.Column(db.String(30), nullable=True)
    source_image = db.Column(db.String(512), nullable=True)  # path to uploaded scoresheet image
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    league = db.relationship("League", backref="fixtures")
    rubbers = db.relationship(
        "Rubber", backref="fixture", lazy="dynamic", cascade="all, delete-orphan"
    )

    def __repr__(self):
        """Return string representation of Fixture."""
        return f"<Fixture {self.home_team} vs {self.away_team} on {self.date}>"


class Rubber(db.Model):
    """A single rubber (individual match) within a fixture.

    A fixture typically has multiple rubbers (e.g. 4 singles + 2 doubles).
    Each rubber has a pair of home players and a pair of away players,
    plus set scores.
    """

    __tablename__ = "rubbers"

    id = db.Column(db.Integer, primary_key=True)
    fixture_id = db.Column(db.Integer, db.ForeignKey("fixtures.id"), nullable=False)
    rubber_number = db.Column(db.Integer, nullable=False)  # 1-based index within fixture
    rubber_type = db.Column(db.String(20), default="singles")  # singles or doubles
    home_player_1 = db.Column(db.String(120), nullable=True)
    home_player_2 = db.Column(db.String(120), nullable=True)
    home_set1 = db.Column(db.Integer, nullable=True)
    away_set1 = db.Column(db.Integer, nullable=True)
    set1_tie = db.Column(db.Boolean, default=False, nullable=True)
    set1_walkover = db.Column(db.String(10), nullable=True)  # 'home', 'away', or None
    home_set2 = db.Column(db.Integer, nullable=True)
    away_set2 = db.Column(db.Integer, nullable=True)
    set2_tie = db.Column(db.Boolean, default=False, nullable=True)
    home_set3 = db.Column(db.Integer, nullable=True)
    away_set3 = db.Column(db.Integer, nullable=True)
    set3_tie = db.Column(db.Boolean, default=False, nullable=True)
    winner = db.Column(db.String(10), nullable=True)  # "home", "away", or "tie"

    player_results = db.relationship(
        "PlayerMatchResult", backref="rubber", lazy="dynamic", cascade="all, delete-orphan"
    )

    def __repr__(self):
        """Return string representation of Rubber."""
        return f"<Rubber {self.rubber_number} of Fixture {self.fixture_id}>"


class PlayerMatchResult(db.Model):
    """Links a player to a rubber result, recording their individual activity."""

    __tablename__ = "player_match_results"

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=False)
    rubber_id = db.Column(db.Integer, db.ForeignKey("rubbers.id"), nullable=False)
    side = db.Column(db.String(10), nullable=False)  # "home" or "away"
    won = db.Column(db.Boolean, default=False)

    __table_args__ = (db.UniqueConstraint("player_id", "rubber_id", name="uq_player_rubber"),)

    @property
    def is_walkover(self):
        """Return True if this result was from a walkover rubber."""
        return bool(self.rubber.set1_walkover)

    def __repr__(self):
        """Return string representation of PlayerMatchResult."""
        return f"<PlayerMatchResult player={self.player_id} rubber={self.rubber_id}>"


class TeamCaptain(db.Model):
    """Records who is captain of a team for a given season.

    At most one captain per team per season. A player may captain multiple teams.
    """

    __tablename__ = "team_captains"

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    season = db.Column(db.String(20), nullable=False, default="2026")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    player = db.relationship("Player", backref="captaincies")
    team = db.relationship("Team", backref="captains")

    __table_args__ = (db.UniqueConstraint("team_id", "season", name="uq_team_captain_season"),)

    def __repr__(self):
        """Return string representation of TeamCaptain."""
        return f"<TeamCaptain player={self.player_id} team={self.team_id} season={self.season}>"


class FixtureSquadEntry(db.Model):
    """A player reserved in the squad for a fixture by the captain."""

    __tablename__ = "fixture_squad_entries"

    id = db.Column(db.Integer, primary_key=True)
    fixture_id = db.Column(db.Integer, db.ForeignKey("fixtures.id"), nullable=False)
    player_id = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    player = db.relationship("Player", backref="squad_entries")
    fixture = db.relationship("Fixture", backref="squad_entries")
    team = db.relationship("Team", backref="squad_entries")

    __table_args__ = (
        db.UniqueConstraint("fixture_id", "player_id", name="uq_fixture_player_squad"),
    )

    def __repr__(self):
        """Return string representation of FixtureSquadEntry."""
        return f"<FixtureSquadEntry fixture={self.fixture_id} player={self.player_id}>"


class MatchFeePaid(db.Model):
    """Records that a player has paid their match fee for a fixture."""

    __tablename__ = "match_fees_paid"

    id = db.Column(db.Integer, primary_key=True)
    fixture_id = db.Column(db.Integer, db.ForeignKey("fixtures.id"), nullable=False)
    player_id = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=True)
    paid_at = db.Column(db.DateTime, default=datetime.utcnow)

    player = db.relationship("Player", backref="match_fees")
    fixture = db.relationship("Fixture", backref="match_fees")
    team = db.relationship("Team", backref="match_fees")

    __table_args__ = (db.UniqueConstraint("fixture_id", "player_id", name="uq_fee_fixture_player"),)

    def __repr__(self):
        """Return string representation of MatchFeePaid."""
        return f"<MatchFeePaid fixture={self.fixture_id} player={self.player_id}>"


class CaptainUser(db.Model):
    """Maps a Firebase UID to a player, granting captain-level access.

    All Firebase users without a CaptainUser record are treated as admins. A
    CaptainUser is linked to a Player who has TeamCaptain records determining
    which teams they can manage.

    Rows with `sees_all_fixtures=True` are a special case: they exist purely so an
    admin can also use the pitchside match-day login (which looks players up by
    CaptainUser.email + Player.lta_number). Their `uid` is a synthetic value, not a
    real Firebase UID, so they're never found by `CaptainUser.query.get(<real uid>)`
    at normal sign-in — the admin's real role is unaffected. Unlike an ordinary
    captain, they aren't restricted to fixtures involving a team they captain.
    """

    __tablename__ = "captain_users"

    uid = db.Column(db.String(128), primary_key=True)
    email = db.Column(db.String(200), nullable=False)
    player_id = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=True)
    sees_all_fixtures = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    player = db.relationship("Player", backref="captain_login")

    def __repr__(self):
        """Return string representation of CaptainUser."""
        return f"<CaptainUser uid={self.uid} email={self.email}>"


class MatchDayDraft(db.Model):
    """A pitchside, captain-entered draft scoresheet for a fixture.

    Captains fill this in from their phone during a match via a separate lightweight
    login (email + last-4 LTA digits), as a memory aid for the paper scorecard. It never
    updates the real Rubber/PlayerMatchResult rows directly — an admin or captain must
    log in normally afterwards and confirm it via the manual fixture entry form, which
    is pre-filled from this draft's data.
    """

    __tablename__ = "match_day_drafts"

    id = db.Column(db.Integer, primary_key=True)
    fixture_id = db.Column(db.Integer, db.ForeignKey("fixtures.id"), nullable=False, unique=True)
    status = db.Column(db.String(20), nullable=False, default="draft")  # draft/submitted/confirmed
    data = db.Column(db.Text, nullable=True)  # JSON-encoded {"rubbers": [...]}
    started_by_player_id = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=True)
    submitted_by_player_id = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_saved_at = db.Column(db.DateTime, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    confirmed_at = db.Column(db.DateTime, nullable=True)

    fixture = db.relationship("Fixture", backref=db.backref("match_day_draft", uselist=False))
    started_by = db.relationship("Player", foreign_keys=[started_by_player_id])
    submitted_by = db.relationship("Player", foreign_keys=[submitted_by_player_id])

    def __repr__(self):
        """Return string representation of MatchDayDraft."""
        return f"<MatchDayDraft fixture={self.fixture_id} status={self.status}>"


class AppSetting(db.Model):
    """Key-value store for application-level metadata."""

    __tablename__ = "app_settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(256), nullable=True)

    def __repr__(self):
        """Return string representation of AppSetting."""
        return f"<AppSetting {self.key}={self.value}>"


class LoginEvent(db.Model):
    """Records each login and logout for auditing purposes."""

    __tablename__ = "login_events"

    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    logged_in_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    logged_out_at = db.Column(db.DateTime, nullable=True)
    logout_reason = db.Column(db.String(20), nullable=True)  # 'manual' | 'timeout'

    def __repr__(self):
        """Return string representation of LoginEvent."""
        return f"<LoginEvent uid={self.uid} at={self.logged_in_at}>"


# ── Helper: round label ──────────────────────────────────────────────────


def fixture_round_label(
    fixture_date: date,
    league_start_date,
    catchup_week_starts=None,
) -> str | None:
    """Return the round label for a fixture given the league start date.

    catchup_week_starts is an iterable of Saturdays that are catch-up weeks (no
    rounds played) — any number of them, anywhere in the season, or none at all
    for a league that runs in consecutive rounds throughout.

    Returns 'Round N', 'Catch-up Week', or None if no start date or the fixture
    date is before the season begins.
    """
    if not league_start_date or fixture_date < league_start_date:
        return None

    catchup_set = set(catchup_week_starts or [])
    week_start = league_start_date
    round_num = 1
    while True:
        week_end = week_start + timedelta(days=7)
        if week_start <= fixture_date < week_end:
            return "Catch-up Week" if week_start in catchup_set else f"Round {round_num}"
        if week_start not in catchup_set:
            round_num += 1
        week_start = week_end


# ── Rubber plan: shape/pairing per league format ────────────────────────────


class RubberSlot:
    """One rubber's shape/pairing within a fixture's rubber plan.

    `home_slots`/`away_slots` are 2-tuples of squad-slot keys ("pair1_man",
    "pair1_woman", "pair2_man", "pair2_woman") naming which of *that side's own*
    4 fixed squad players fill this rubber. They differ between home and away
    only for cleveland_veterans' two "cross" mixed rubbers (pair 1 faces the
    opponent's pair 2, and vice versa) — every other rubber in both fixed-squad
    formats is symmetric. Both are None for cleveland_autumn, whose pairing is
    computed dynamically elsewhere (the pairs rotation in save_rubbers and the
    matching JS in pitchside_entry.html) — this plan only fixes its shape.
    """

    def __init__(self, rubber_number, label, sets, home_slots=None, away_slots=None):
        """Store this slot's physical rubber_number, display label, and shape."""
        self.rubber_number = rubber_number
        self.label = label
        self.sets = sets
        self.home_slots = home_slots
        self.away_slots = away_slots


def build_rubber_plan(league):
    """Return the ordered rubber plan (list of RubberSlot) for a league's format.

    Single source of truth for rubber count/labels/sets-per-rubber across all
    three formats, shared by ICS import, fixture_detail's squad size/grid,
    edit_fixture's walkover max score, save_rubbers, and pitchside_entry.
    """
    fmt = (league.format if league else None) or LEAGUE_FORMAT_CLEVELAND_AUTUMN
    if fmt == LEAGUE_FORMAT_CLEVELAND_VETERANS:
        mens = ("pair1_man", "pair2_man")
        womens = ("pair1_woman", "pair2_woman")
        p1 = ("pair1_man", "pair1_woman")
        p2 = ("pair2_man", "pair2_woman")
        return [
            RubberSlot(1, "Mens Doubles", 1, mens, mens),
            RubberSlot(2, "Womens Doubles", 1, womens, womens),
            RubberSlot(3, "Mixed 1 v Mixed 1", 1, p1, p1),
            RubberSlot(4, "Mixed 1 v Mixed 2", 1, p1, p2),
            RubberSlot(5, "Mixed 2 v Mixed 1", 1, p2, p1),
            RubberSlot(6, "Mixed 2 v Mixed 2", 1, p2, p2),
        ]
    if fmt == LEAGUE_FORMAT_NE_WINTER:
        mens = ("pair1_man", "pair2_man")
        womens = ("pair1_woman", "pair2_woman")
        p1 = ("pair1_man", "pair1_woman")
        p2 = ("pair2_man", "pair2_woman")
        return [
            RubberSlot(1, "Mens Doubles", 2, mens, mens),
            RubberSlot(2, "Womens Doubles", 2, womens, womens),
            RubberSlot(3, "Mixed Pair 1", 2, p1, p1),
            RubberSlot(4, "Mixed Pair 2", 2, p2, p2),
        ]
    # cleveland_autumn (default) — a genuine round-robin: each of the
    # n_pairs home pairs plays each of the n_pairs away pairs exactly once,
    # so there are always n_pairs rounds (not a fixed 3 — that only ever
    # coincided with reality for the historical 3-pairs-per-side default;
    # real Autumn leagues now use 2 pairs and 4 rubbers). Matches
    # save_rubbers' sequential numbering: pair i's n_pairs rounds occupy a
    # stride of n_pairs (rubber_number = i*n_pairs + n).
    #
    # sets_per_rubber is cascaded from the parent league and, despite its
    # "descriptive only" column comment, is what actually distinguishes
    # Summer's single set to games_per_set (e.g. 1 set to 8, no decider)
    # from Autumn's 2-sets-to-6-plus-champion's-tiebreak.
    n_pairs = (league.pairs_per_round if league else None) or 3
    parent = league.parent_league if league else None
    sets = (parent.sets_per_rubber if parent and parent.sets_per_rubber else None) or 2
    return [
        RubberSlot((i * n_pairs) + n, f"Pair {i + 1} vs Opposition {n}", sets)
        for i in range(n_pairs)
        for n in range(1, n_pairs + 1)
    ]


def league_format_description(parent) -> str:
    """Plain-text description of a parent league's rubber structure and scoring.

    Shown on the Parent Leagues cards in place of the old pairs/sets/games
    badges, which only ever rendered for cleveland_autumn. `parent` is a
    ParentLeague (or anything duck-typed with the same format/pairs_per_round/
    games_per_set/tiebreak_at_games fields).
    """
    games = parent.games_per_set
    tiebreak = parent.tiebreak_at_games
    scoring = (
        f"Rubbers are played first to {games} games, with a 7-point tie-break "
        f"at {tiebreak}-{tiebreak}."
    )
    if parent.format == LEAGUE_FORMAT_CLEVELAND_VETERANS:
        return (
            "Teams of 2 women and 2 men. 6 rubbers. Opening 2 rubbers are men's and "
            "women's doubles. Rubbers 3-6 are Mixed 1 v Mixed 1, Mixed 1 v Mixed 2, "
            "Mixed 2 v Mixed 1, Mixed 2 v Mixed 2. " + scoring
        )
    if parent.format == LEAGUE_FORMAT_NE_WINTER:
        return (
            "Teams of 2 women and 2 men. 4 rubbers of 2 sets: men's doubles, women's "
            "doubles, and Mixed 1 vs Mixed 1 and Mixed 2 vs Mixed 2. "
            "All sets are played to 6 games. No tie-breaks."
        )
    n_pairs = parent.pairs_per_round or 3
    sets = parent.sets_per_rubber or 2
    intro = (
        f"{n_pairs} pairs per side with each home pair playing each away pair i.e. "
        f"{n_pairs * n_pairs} rubbers. "
    )
    if sets <= 1:
        # A genuinely single-set rubber (e.g. Summer): no second set, no decider.
        return intro + (
            f"Each rubber is a single set to {games} games, with a "
            f"7-point tie-break at {tiebreak}-{tiebreak}."
        )
    return intro + (
        f"Each rubber is {sets} sets to {games} games, with a "
        f"7-point tie-break at {tiebreak}-{tiebreak}. If the sets are shared 1-1, a champion's "
        "tie-break to 10 points decides the rubber."
    )


def rubber_count_for_league(league) -> int:
    """Return the number of rubbers a fixture in this league's format has."""
    return len(build_rubber_plan(league))


def walkover_max_score(league) -> int:
    """Max fixture score awarded to the winner of a recorded walkover.

    cleveland_autumn/cleveland_veterans: 2 pts/rubber win + 4 bonus pts.
    ne_winter: pure set points, no bonus — max is simply total sets in the plan.
    """
    fmt = (league.format if league else None) or LEAGUE_FORMAT_CLEVELAND_AUTUMN
    plan = build_rubber_plan(league)
    if fmt == LEAGUE_FORMAT_NE_WINTER:
        return sum(slot.sets for slot in plan)
    return len(plan) * 2 + 4


def score_from_rubber_results(rubbers) -> tuple[int, int]:
    """Tally fixture score from rubber-level results.

    2 pts per rubber win, 1 pt each side for a tied rubber, plus 4 bonus pts
    to the side winning more rubbers overall (split 2-2 if equal). `rubbers`
    is any iterable of objects with a `.winner` attribute in ('home', 'away',
    'tie', None). Used by cleveland_autumn and cleveland_veterans — the two
    formats that share this same rubber-count-based formula.
    """
    home_wins = away_wins = ties = 0
    for r in rubbers:
        if r.winner == "tie":
            ties += 1
        elif r.winner == "home":
            home_wins += 1
        elif r.winner == "away":
            away_wins += 1
    if home_wins > away_wins:
        home_bonus, away_bonus = 4, 0
    elif away_wins > home_wins:
        home_bonus, away_bonus = 0, 4
    else:
        home_bonus, away_bonus = 2, 2
    return (home_wins * 2 + ties + home_bonus, away_wins * 2 + ties + away_bonus)


def score_from_set_points(rubber_slot_pairs) -> tuple[int, int]:
    """Tally fixture score as pure set points.

    1 pt per set won, summed across every rubber, with no rubber-level or
    match-level bonus. `rubber_slot_pairs` is an iterable of (Rubber,
    RubberSlot) pairs. A rubber with `set1_walkover` set awards its full
    `slot.sets` points to the walkover winner. Otherwise each of the rubber's
    `set{n}_home`/`set{n}_away` pairs is compared directly. Also sets each
    rubber's `.winner` to 'home'/'away'/'tie' for display purposes only (e.g.
    2-0, 0-2, or 1-1 in sets) — this does not affect the returned point
    totals. Used only by ne_winter.
    """
    home_points = away_points = 0
    for rubber, slot in rubber_slot_pairs:
        if rubber.set1_walkover in ("home", "away"):
            if rubber.set1_walkover == "home":
                home_points += slot.sets
                rubber.winner = "home"
            else:
                away_points += slot.sets
                rubber.winner = "away"
            continue
        home_sets_won = away_sets_won = 0
        for n in range(1, slot.sets + 1):
            h = getattr(rubber, f"home_set{n}")
            a = getattr(rubber, f"away_set{n}")
            if h is None or a is None:
                continue
            if h > a:
                home_points += 1
                home_sets_won += 1
            elif a > h:
                away_points += 1
                away_sets_won += 1
        if home_sets_won > away_sets_won:
            rubber.winner = "home"
        elif away_sets_won > home_sets_won:
            rubber.winner = "away"
        else:
            rubber.winner = "tie"
    return (home_points, away_points)


def rubber_winner_from_sets(
    home_set1, away_set1, home_set2, away_set2, home_tiebreak=None, away_tiebreak=None
) -> str | None:
    """Winner of a rubber from 2 sets plus an optional champion tiebreak.

    Used by cleveland_autumn: 2 sets to 6 games, 7-point tiebreak at 6-6; if
    the sets split 1-1, a 10-point champion tiebreak decides the rubber
    instead of a full third set. A set is won by whoever has the higher
    score — an unset (None) or equal score decides nothing. If each side
    wins exactly one set, home_tiebreak/away_tiebreak decides; otherwise
    they're ignored. Returns 'home', 'away', or None if not yet decided.

    Degenerately also correct for a single-set rubber (only home_set1/
    away_set1 given, home_set2/away_set2 both None) — used as the pitchside
    confirm-step winner check for cleveland_veterans, whose rubbers really
    are just one set.
    """

    def _set_winner(h, a):
        if h is None or a is None or h == a:
            return None
        return "home" if h > a else "away"

    s1 = _set_winner(home_set1, away_set1)
    if home_set2 is None and away_set2 is None:
        # No second set at all (a genuinely single-set rubber, e.g.
        # cleveland_veterans) — set 1 alone decides.
        return s1

    s2 = _set_winner(home_set2, away_set2)
    home_sets = [s1, s2].count("home")
    away_sets = [s1, s2].count("away")
    if home_sets == 2:
        return "home"
    if away_sets == 2:
        return "away"
    if home_sets == 1 and away_sets == 1:
        return _set_winner(home_tiebreak, away_tiebreak)
    return None


# ── Helper: league display grouping ────────────────────────────────────────

_LEAGUE_GENDER_LABELS = {"men": "Men's", "women": "Women's", "mixed": "Mixed"}
_LEAGUE_GENDER_RANK = {"men": 0, "women": 1, "mixed": 2, "other": 3}


def _league_gender_category(name: str) -> str:
    """Classify a league name as 'men', 'women', 'mixed', or 'other'.

    Mirrors the same ad-hoc name-based classification already used for
    grouping teams and divisions elsewhere in the templates, kept here so the
    Leagues page can share it.
    """
    if "Women" in name:
        return "women"
    if "Mixed" in name:
        return "mixed"
    if "Men" in name:
        return "men"
    return "other"


def _league_card_label(league) -> str:
    """Return the short label ('Men's' / 'Women's' / 'Mixed') for a league's card.

    Falls back to the full league name for names that don't fit the pattern,
    since a bare 'Other' label wouldn't mean anything to a reader.
    """
    return _LEAGUE_GENDER_LABELS.get(_league_gender_category(league.name), league.name)


def _league_year(league) -> int | None:
    """Best-effort season year for a league.

    A trailing 4-digit year in its name, else the year of its start date,
    else None.
    """
    parts = league.name.split()
    if parts and parts[-1].isdigit():
        return int(parts[-1])
    if league.start_date:
        return league.start_date.year
    return None


def group_leagues_for_display(leagues) -> list[dict]:
    """Group leagues into season sections for the Leagues page.

    Leagues that share a parent league and season year (e.g. the Men's,
    Women's, and Mixed editions of "Cleveland ... Summer League 2026") are
    grouped into one section per season/parent-league combination, so a
    reader sees "Summer League 2026" once with its three gender editions
    underneath, rather than a flat, alphabetically-jumbled list of similarly
    named leagues. Sections are ordered newest season first, and chronologically
    within a shared year (so a Summer section sorts before an Autumn one).
    Leagues with no parent league each get their own single-league section,
    appended at the end in alphabetical order.

    Each group also carries an "expanded" flag: only the newest season section
    per parent league defaults open (e.g. 2027 expanded, 2026 and earlier
    collapsed but still one click away) once that parent league has more than
    one season on record — this is what keeps the page from becoming a wall of
    open sections once a league has run for several years. Ungrouped
    (no-parent-league) sections always default open since they're one-off,
    single-league sections rather than a season history.
    """
    grouped: dict = {}
    order: list = []
    ungrouped = []
    for league in leagues:
        if league.parent_league:
            year = _league_year(league)
            key = (league.parent_league_id, year)
            if key not in grouped:
                grouped[key] = {
                    "label": league.parent_league.name + (f" {year}" if year else ""),
                    "colour": league.parent_league.colour,
                    "year": year,
                    "start_date": league.start_date,
                    "parent_league_id": league.parent_league_id,
                    "leagues": [],
                }
                order.append(key)
            group = grouped[key]
            group["leagues"].append({"league": league, "card_label": _league_card_label(league)})
            if league.start_date and (
                group["start_date"] is None or league.start_date < group["start_date"]
            ):
                group["start_date"] = league.start_date
        else:
            ungrouped.append(league)

    groups = [grouped[key] for key in order]
    for group in groups:
        group["leagues"].sort(
            key=lambda entry: (
                _LEAGUE_GENDER_RANK[_league_gender_category(entry["league"].name)],
                entry["league"].name,
            )
        )
    groups.sort(key=lambda g: (-(g["year"] or 0), g["start_date"] or date.max))

    seen_parent_ids = set()
    for group in groups:
        pid = group["parent_league_id"]
        group["expanded"] = pid not in seen_parent_ids
        seen_parent_ids.add(pid)

    for league in sorted(ungrouped, key=lambda lg: lg.name):
        groups.append(
            {
                "label": league.name,
                "colour": "#6c757d",
                "year": _league_year(league),
                "start_date": league.start_date,
                "parent_league_id": None,
                "expanded": True,
                "leagues": [{"league": league, "card_label": _league_card_label(league)}],
            }
        )
    return groups


def current_season_group(season_type) -> dict | None:
    """Return the most recent season's Leagues for a nav badge (S/A/V/W), or None.

    Scans every ParentLeague tagged with this season_type, finds the most recent
    year among their Leagues (via _league_year, the same grouping used by
    group_leagues_for_display), and returns that year's League rows together —
    e.g. the Men's/Women's/Mixed editions of the current Autumn League as one
    group. None if no ParentLeague of this type has any Leagues yet (e.g.
    Veterans/Winter before their first season). Badge colour is not derived
    here — see SEASON_TYPE_COLOURS, a fixed palette independent of whatever
    colour the underlying ParentLeague happens to have.
    """
    if not season_type:
        return None
    parent_ids = [p.id for p in ParentLeague.query.filter_by(season_type=season_type).all()]
    if not parent_ids:
        return None
    leagues = League.query.filter(League.parent_league_id.in_(parent_ids)).all()
    if not leagues:
        return None
    latest = max(leagues, key=lambda lg: lg.start_date or date.min)
    target_year = _league_year(latest)
    season_leagues = sorted(
        (lg for lg in leagues if _league_year(lg) == target_year), key=lambda lg: lg.name
    )
    return {
        "year": target_year,
        "leagues": season_leagues,
    }


def current_leagues_for_season_type(season_type) -> list:
    """Return the League rows for the most recent season of a season_type, or []."""
    group = current_season_group(season_type)
    return group["leagues"] if group else []


# ── Helper: eligibility check ─────────────────────────────────────────────


def _division_rank(division_name: str) -> float:
    """Parse the numeric tier from a name like 'Division 4'. Higher number = lower tier."""
    try:
        return int(division_name.split()[-1])
    except (ValueError, AttributeError):
        return float("inf")


def _team_rank(team) -> tuple:
    """Return (division_rank, letter_rank) for hierarchical comparison.

    Lower tuple = higher in hierarchy. Division rank is primary; within a division,
    team letter A=0 < B=1 < ... < F=5 is secondary (A is the highest-ranked team).
    """
    div_rank = _division_rank(team.division.name) if team.division else float("inf")
    m = re.search(r"\b([A-Z])\b", team.name) if team.name else None
    letter_rank = ord(m.group(1)) - ord("A") if m else 0
    return (div_rank, letter_rank)


def check_player_eligibility(player_id: int, team_id: int, league_id: int) -> dict:
    """Check whether a player is eligible to play for a team in a league.

    Once a player's *cumulative* fixtures for a team and every higher-ranked team
    (team rank is determined by division first, then letter within a division)
    reach the team_commitment_threshold, they are locked to that team. They may
    move up to a higher-ranked team but cannot play for any team ranked the same
    or lower (e.g. 1 fixture for team A plus 2 for team C reaches a threshold of
    3, locking the player to team C even though neither team alone hit the
    threshold; they're then blocked from team D and below).

    Returns a dict with:
        - eligible (bool): whether the player can play
        - current_fixtures (int): fixtures played for this specific team in this league
        - commitment_threshold (int|None): the fixture threshold for team commitment
        - restriction_description (str|None): human-readable rule
        - committed_to_different_team (bool): ineligible due to team commitment
        - effective_committed_team_id (int|None): id of the team the player is tied to
        - commitment_info (str|None): name of the committed team if already committed here
    """
    from sqlalchemy import func

    # Count unique fixtures where the player played *for* this team (side-matched).
    current_fixtures = (
        db.session.query(func.count(func.distinct(Fixture.id)))
        .join(Rubber, Fixture.id == Rubber.fixture_id)
        .join(PlayerMatchResult, Rubber.id == PlayerMatchResult.rubber_id)
        .filter(
            PlayerMatchResult.player_id == player_id,
            Fixture.league_id == league_id,
            db.or_(
                db.and_(Fixture.home_team_id == team_id, PlayerMatchResult.side == "home"),
                db.and_(Fixture.away_team_id == team_id, PlayerMatchResult.side == "away"),
            ),
        )
        .scalar()
        or 0
    )

    # Look up the league restriction
    restriction = LeagueRestriction.query.filter_by(league_id=league_id).first()
    commitment_threshold = None
    restriction_description = None
    committed_to_different_team = False
    commitment_info = None
    effective_committed_team = None

    if restriction and restriction.team_commitment_threshold > 0:
        commitment_threshold = restriction.team_commitment_threshold
        restriction_description = restriction.description or (
            f"Player tied to a team after {commitment_threshold} fixtures; "
            "can only play for a higher-ranked team"
        )

        existing_commitment = PlayerTeamCommitment.query.filter_by(
            player_id=player_id, league_id=league_id
        ).first()

        # Determine the natural committed team: the most senior team (lowest
        # division, then A before B, ...) such that the player's *cumulative*
        # fixtures for that team and every higher-ranked team reach the threshold.
        # e.g. 1 fixture for team A + 2 for team C = 3 cumulative through C, so a
        # threshold of 3 ties the player to C even though no single team hit 3 alone.
        all_league_teams = (
            Team.query.join(Division, Team.division_id == Division.id)
            .filter(Division.league_id == league_id)
            .all()
        )
        all_league_teams.sort(key=_team_rank)
        natural_committed_team = None
        running_total = 0
        for lt in all_league_teams:
            lt_count = (
                db.session.query(func.count(func.distinct(Fixture.id)))
                .join(Rubber, Fixture.id == Rubber.fixture_id)
                .join(PlayerMatchResult, Rubber.id == PlayerMatchResult.rubber_id)
                .filter(
                    PlayerMatchResult.player_id == player_id,
                    Fixture.league_id == league_id,
                    db.or_(
                        db.and_(Fixture.home_team_id == lt.id, PlayerMatchResult.side == "home"),
                        db.and_(Fixture.away_team_id == lt.id, PlayerMatchResult.side == "away"),
                    ),
                )
                .scalar()
                or 0
            )
            running_total += lt_count
            if running_total >= commitment_threshold:
                natural_committed_team = lt
                break

        # The effective commitment is the more senior of the formal record and the
        # naturally-derived one — this handles stale formal records (e.g. committed
        # to Div 2 formally but has since crossed the threshold for a Div 1 team).
        effective_committed_team = existing_commitment.team if existing_commitment else None
        effective_committed_rank = (
            _team_rank(effective_committed_team)
            if effective_committed_team
            else (float("inf"), float("inf"))
        )
        if natural_committed_team is not None:
            natural_rank = _team_rank(natural_committed_team)
            if natural_rank < effective_committed_rank:
                effective_committed_team = natural_committed_team
                effective_committed_rank = natural_rank

        if effective_committed_team:
            if (
                existing_commitment
                and existing_commitment.team_id == team_id
                and effective_committed_team.id == team_id
            ):
                commitment_info = effective_committed_team.name
            elif effective_committed_team.id != team_id:
                target_team = Team.query.get(team_id)
                if target_team:
                    target_rank = _team_rank(target_team)
                    if target_rank >= effective_committed_rank:
                        committed_to_different_team = True
                        committed_div = effective_committed_team.division
                        restriction_description = (
                            f"Tied to {effective_committed_team.name}"
                            + (f" ({committed_div.name})" if committed_div else "")
                            + " — can only play for hierarchically higher teams."
                        )
                else:
                    committed_to_different_team = True
                    restriction_description = (
                        f"Tied to {effective_committed_team.name} in this league"
                    )
            else:
                commitment_info = effective_committed_team.name

    return {
        "eligible": not committed_to_different_team,
        "current_fixtures": current_fixtures,
        "commitment_threshold": commitment_threshold,
        "restriction_description": restriction_description,
        "committed_to_different_team": committed_to_different_team,
        "commitment_info": commitment_info,
        "effective_committed_team_id": (
            effective_committed_team.id if effective_committed_team else None
        ),
    }


def check_week_conflict(player_id: int, fixture_date: date, exclude_fixture_id: int):
    """Return the conflicting Fixture if the player is committed to another fixture this week.

    Checks both confirmed rubber results and squad reservations in other fixtures
    that fall in the same Mon–Sun week as fixture_date. Returns None if no conflict.
    """
    week_start = fixture_date - timedelta(days=fixture_date.weekday())
    week_end = week_start + timedelta(days=6)

    rubber_fixture = (
        db.session.query(Fixture)
        .join(Rubber, Fixture.id == Rubber.fixture_id)
        .join(PlayerMatchResult, Rubber.id == PlayerMatchResult.rubber_id)
        .filter(
            PlayerMatchResult.player_id == player_id,
            Fixture.id != exclude_fixture_id,
            Fixture.date >= week_start,
            Fixture.date <= week_end,
        )
        .first()
    )
    if rubber_fixture:
        return rubber_fixture

    squad_fixture_id = (
        db.session.query(FixtureSquadEntry.fixture_id)
        .join(Fixture, FixtureSquadEntry.fixture_id == Fixture.id)
        .filter(
            FixtureSquadEntry.player_id == player_id,
            FixtureSquadEntry.fixture_id != exclude_fixture_id,
            Fixture.date >= week_start,
            Fixture.date <= week_end,
        )
        .first()
    )
    if squad_fixture_id:
        return Fixture.query.get(squad_fixture_id[0])
    return None


def teams_query_for_league(league_ids):
    """Return a Team query scoped to one or more leagues' divisions, or all teams if falsy.

    Accepts a single league id or a list of ids (e.g. every gender edition under one
    season badge) — a bare int is normalised to a one-item list.
    """
    if not league_ids:
        return Team.query
    if isinstance(league_ids, int):
        league_ids = [league_ids]
    return Team.query.join(Division, Team.division_id == Division.id).filter(
        Division.league_id.in_(league_ids)
    )


def fixtures_query_for_league(league_ids):
    """Return a Fixture query scoped to one or more leagues, or all fixtures if falsy.

    Matches a fixture whose own league_id is one of the given leagues. A fixture with
    no league_id recorded falls back to whichever league its home/away team's division
    currently belongs to — mirroring the effective-league fallback used when grouping
    fixtures for display. The fallback only applies when league_id is unset, so a
    fixture explicitly tagged with one league stays there even if its team is later
    reallocated to a different league's division. Accepts a single id or a list.
    """
    if not league_ids:
        return Fixture.query
    if isinstance(league_ids, int):
        league_ids = [league_ids]
    team_ids = [t.id for t in teams_query_for_league(league_ids).all()]
    conditions = [Fixture.league_id.in_(league_ids)]
    if team_ids:
        team_match = db.or_(Fixture.home_team_id.in_(team_ids), Fixture.away_team_id.in_(team_ids))
        conditions.append(db.and_(Fixture.league_id.is_(None), team_match))
    return Fixture.query.filter(db.or_(*conditions))


def players_query_for_league(league_ids):
    """Return a Player query scoped to rosters of teams in one or more leagues, or all if falsy."""
    if not league_ids:
        return Player.query
    if isinstance(league_ids, int):
        league_ids = [league_ids]
    team_ids = [t.id for t in teams_query_for_league(league_ids).all()]
    if not team_ids:
        return Player.query.filter(db.false())
    return Player.query.filter(Player.teams.any(Team.id.in_(team_ids)))
