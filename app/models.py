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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    divisions = db.relationship(
        "Division", backref="league", lazy="dynamic", cascade="all, delete-orphan"
    )
    restrictions = db.relationship(
        "LeagueRestriction", backref="league", lazy="dynamic", cascade="all, delete-orphan"
    )

    def delete_league(self):
        """Delete the league and cascade deletions to associated divisions, teams, etc."""
        db.session.delete(self)
        db.session.commit()
        return True


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

    def delete_team(self):
        """Delete the team and cascade deletions to associated player commitments."""
        db.session.delete(self)
        db.session.commit()
        return True


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

    All Firebase users without a CaptainUser record are treated as admins.
    A CaptainUser is linked to a Player who has TeamCaptain records determining
    which teams they can manage.
    """

    __tablename__ = "captain_users"

    uid = db.Column(db.String(128), primary_key=True)
    email = db.Column(db.String(200), nullable=False)
    player_id = db.Column(db.Integer, db.ForeignKey("players.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    player = db.relationship("Player", backref="captain_login")

    def __repr__(self):
        """Return string representation of CaptainUser."""
        return f"<CaptainUser uid={self.uid} email={self.email}>"


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
    round5_start_date=None,
    round9_start_date=None,
) -> str | None:
    """Return the round label for a fixture given the league start date.

    When round5_start_date / round9_start_date are provided, catch-up weeks are
    inferred as the week immediately before each of those dates.  Otherwise falls
    back to the original hardcoded schedule: Rounds 1-4, catch-up week,
    Rounds 5-8, catch-up week, then Rounds 9+.

    Returns 'Round N', 'Catch-up Week', or None if no start date or the fixture
    date is before the season begins.
    """
    if not league_start_date or fixture_date < league_start_date:
        return None

    if round5_start_date or round9_start_date:
        catchup1_start = round5_start_date - timedelta(days=7) if round5_start_date else None
        catchup2_start = round9_start_date - timedelta(days=7) if round9_start_date else None

        if round9_start_date and fixture_date >= round9_start_date:
            week = (fixture_date - round9_start_date).days // 7
            return f"Round {week + 9}"
        if catchup2_start and fixture_date >= catchup2_start:
            return "Catch-up Week"
        if round5_start_date and fixture_date >= round5_start_date:
            week = (fixture_date - round5_start_date).days // 7
            return f"Round {week + 5}"
        if catchup1_start and fixture_date >= catchup1_start:
            return "Catch-up Week"
        # Before first catch-up week
        week = (fixture_date - league_start_date).days // 7
        return f"Round {week + 1}"

    # Fallback: original hardcoded offsets
    week = (fixture_date - league_start_date).days // 7
    if week < 4:
        return f"Round {week + 1}"
    elif week == 4:
        return "Catch-up Week"
    elif week < 9:
        return f"Round {week}"
    elif week == 9:
        return "Catch-up Week"
    else:
        return f"Round {week - 1}"


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

    Once a player reaches the team_commitment_threshold for a team, they are tied to
    that specific team. They may move up to a higher-ranked division (lower division
    number) but cannot play for any team in the same or a lower-ranked division
    (e.g. committed to a Division 3 team, blocked from all other Division 3 and Division 4+ teams).

    Returns a dict with:
        - eligible (bool): whether the player can play
        - current_fixtures (int): fixtures played for this specific team in this league
        - commitment_threshold (int|None): the fixture threshold for team commitment
        - restriction_description (str|None): human-readable rule
        - committed_to_different_team (bool): ineligible due to divisional commitment
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

    if restriction and restriction.team_commitment_threshold > 0:
        commitment_threshold = restriction.team_commitment_threshold
        restriction_description = restriction.description or (
            f"Player tied to a team after {commitment_threshold} fixtures; "
            "can only play for teams in a higher division"
        )

        existing_commitment = PlayerTeamCommitment.query.filter_by(
            player_id=player_id, league_id=league_id
        ).first()

        # Determine the effective committed team: the highest-division team
        # where the player either has a formal record or has hit the count threshold.
        # This handles stale formal records (e.g. committed to Div 2 formally but
        # has since crossed the threshold for a Div 1 team).
        effective_committed_team = existing_commitment.team if existing_commitment else None
        effective_committed_rank = (
            _team_rank(effective_committed_team)
            if effective_committed_team
            else (float("inf"), float("inf"))
        )

        # Scan for a count-based commitment that outranks the formal one (higher in hierarchy).
        all_league_teams = (
            Team.query.join(Division, Team.division_id == Division.id)
            .filter(Division.league_id == league_id)
            .all()
        )
        all_league_teams.sort(key=_team_rank)
        for lt in all_league_teams:
            lt_rank = _team_rank(lt)
            if lt_rank >= effective_committed_rank:
                break  # Only care about teams strictly higher than the current commitment
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
            if lt_count >= commitment_threshold:
                effective_committed_team = lt
                effective_committed_rank = lt_rank
                break

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
