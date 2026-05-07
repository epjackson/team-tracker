"""Database models for the tennis team recording app."""

from datetime import datetime

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
    lta_number = db.Column(db.String(30), nullable=False)
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
    home_set1 = db.Column(db.Integer, nullable=True)
    away_set1 = db.Column(db.Integer, nullable=True)
    home_set2 = db.Column(db.Integer, nullable=True)
    away_set2 = db.Column(db.Integer, nullable=True)
    home_set3 = db.Column(db.Integer, nullable=True)
    away_set3 = db.Column(db.Integer, nullable=True)
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

    def __repr__(self):
        """Return string representation of PlayerMatchResult."""
        return f"<PlayerMatchResult player={self.player_id} rubber={self.rubber_id}>"


# ── Helper: eligibility check ─────────────────────────────────────────────


def check_player_eligibility(player_id: int, team_id: int, league_id: int) -> dict:
    """Check whether a player is eligible to play for a team in a league.

    Returns a dict with:
        - eligible (bool): whether the player can play
        - current_fixtures (int): how many fixtures they've already played in
        - commitment_threshold (int|None): the fixture threshold for team commitment
        - restriction_description (str|None): human-readable rule
    """
    from sqlalchemy import func

    # Count unique fixtures this player has played in for this team in this league
    current_fixtures = (
        db.session.query(func.count(func.distinct(Fixture.id)))
        .join(Rubber, Fixture.id == Rubber.fixture_id)
        .join(PlayerMatchResult, Rubber.id == PlayerMatchResult.rubber_id)
        .filter(
            PlayerMatchResult.player_id == player_id,
            Fixture.league_id == league_id,
            Fixture.home_team_id.in_([team_id, None]) | Fixture.away_team_id.in_([team_id, None]),
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

    # Check team commitment restrictions
    if restriction and restriction.team_commitment_threshold > 0:
        commitment_threshold = restriction.team_commitment_threshold
        restriction_description = restriction.description or (
            f"Player tied to first team after {commitment_threshold} fixtures in {league_id}"
        )

        # Look for existing commitment for this player in this league
        existing_commitment = PlayerTeamCommitment.query.filter_by(
            player_id=player_id, league_id=league_id
        ).first()

        if existing_commitment:
            # Player is committed to a team
            if existing_commitment.team_id != team_id:
                # Trying to play for a different team
                committed_to_different_team = True
            else:
                # Already committed to this team - track it
                commitment_info = existing_commitment.team.name

    eligible = True
    if committed_to_different_team:
        eligible = False

    return {
        "eligible": eligible,
        "current_fixtures": current_fixtures,
        "commitment_threshold": commitment_threshold,
        "restriction_description": restriction_description,
        "committed_to_different_team": committed_to_different_team,
        "commitment_info": commitment_info,
    }
