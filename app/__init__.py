"""Flask application factory and route definitions."""

import os
from datetime import date, datetime

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from .models import (
    Division,
    Fixture,
    League,
    LeagueRestriction,
    Player,
    PlayerMatchResult,
    PlayerTeamCommitment,
    Rubber,
    Team,
    check_player_eligibility,
    db,
)


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
        conn.execute(
            text(
                """
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
                """
            )
        )
        conn.execute(text("INSERT INTO fixtures_new SELECT * FROM fixtures"))
        conn.execute(text("DROP TABLE fixtures"))
        conn.execute(text("ALTER TABLE fixtures_new RENAME TO fixtures"))


def create_app(config=None):
    """Create and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=True)

    # Default configuration
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///tennis.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")

    if config:
        app.config.update(config)

    db.init_app(app)

    with app.app_context():
        db.create_all()
        _migrate_away_team_nullable(db)
        _migrate_fixture_team_names(db)

    # ── Leagues ─────────────────────────────────────────────────────────

    @app.route("/leagues")
    def list_leagues():
        leagues = League.query.order_by(League.name).all()
        return render_template("leagues.html", leagues=leagues)

    @app.route("/leagues", methods=["POST"])
    def add_league():
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        early_season_weeks = request.form.get("early_season_weeks", type=int) or 0
        team_commitment_threshold = request.form.get("team_commitment_threshold", type=int) or 0
        if not name:
            flash("League name is required.", "error")
            return redirect(url_for("list_leagues"))
        if League.query.filter_by(name=name).first():
            flash(f"League '{name}' already exists.", "error")
            return redirect(url_for("list_leagues"))
        num_divisions = request.form.get("num_divisions", type=int) or 1
        league = League(name=name, description=description or None)
        db.session.add(league)
        db.session.flush()
        for i in range(1, num_divisions + 1):
            db.session.add(Division(name=f"Division {i}", league_id=league.id))
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
        fixtures = Fixture.query.filter_by(league_id=league.id).order_by(Fixture.date.desc()).all()
        restriction = LeagueRestriction.query.filter_by(league_id=league.id).first()
        return render_template(
            "league_detail.html",
            league=league,
            divisions=divisions,
            fixtures=fixtures,
            restriction=restriction,
        )

    @app.route("/leagues/<int:league_id>/divisions", methods=["POST"])
    def add_division(league_id):
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
        league = League.query.get_or_404(league_id)
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        early_season_weeks = request.form.get("early_season_weeks", type=int) or 0
        team_commitment_threshold = request.form.get("team_commitment_threshold", type=int) or 0

        if not name:
            flash("League name is required.", "error")
            return redirect(url_for("league_detail", league_id=league_id))

        if name != league.name and League.query.filter_by(name=name).first():
            flash(f"League '{name}' already exists.", "error")
            return redirect(url_for("league_detail", league_id=league_id))

        league.name = name
        league.description = description or None

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

    @app.route("/leagues/<int:league_id>/delete", methods=["POST"])
    def delete_league(league_id):
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

    @app.route("/divisions/<int:division_id>/edit", methods=["POST"])
    def edit_division(division_id):
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

    @app.route("/divisions/<int:division_id>/delete", methods=["POST"])
    def delete_division(division_id):
        division = Division.query.get_or_404(division_id)
        league_id = division.league_id
        division_name = division.name
        db.session.delete(division)
        db.session.commit()
        flash(f"Division '{division_name}' and all associated teams have been deleted.", "success")
        return redirect(url_for("league_detail", league_id=league_id))

    # ── Routes ──────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        today = date.today()
        all_fixtures = Fixture.query.all()
        upcoming = (
            Fixture.query.filter(Fixture.date >= today).order_by(Fixture.date.asc()).limit(3).all()
        )
        completed = (
            Fixture.query.filter(Fixture.date < today).order_by(Fixture.date.desc()).limit(3).all()
        )
        teams = Team.query.order_by(Team.name).all()
        leagues = League.query.order_by(League.name).all()
        return render_template(
            "index.html",
            fixtures=all_fixtures,
            upcoming=upcoming,
            completed=completed,
            teams=teams,
            leagues=leagues,
        )

    # ── Teams ───────────────────────────────────────────────────────────

    @app.route("/teams")
    def list_teams():
        teams = Team.query.order_by(Team.name).all()
        divisions = Division.query.order_by(Division.name).all()
        leagues = League.query.order_by(League.name).all()
        return render_template("teams.html", teams=teams, divisions=divisions, leagues=leagues)

    @app.route("/teams", methods=["POST"])
    def add_team():
        name = request.form.get("name", "").strip()
        division_id = request.form.get("division_id", type=int)
        if not name:
            flash("Team name is required.", "error")
            return redirect(url_for("list_teams"))
        team = Team(name=name, division_id=division_id if division_id else None)
        db.session.add(team)
        db.session.commit()
        flash(f"Team '{name}' created.", "success")
        return redirect(url_for("list_teams"))

    @app.route("/teams/<int:team_id>/edit", methods=["POST"])
    def edit_team(team_id):
        team = Team.query.get_or_404(team_id)
        name = request.form.get("name", "").strip()
        division_id = request.form.get("division_id", type=int)

        if not name:
            flash("Team name is required.", "error")
            return redirect(url_for("team_detail", team_id=team_id))

        team.name = name
        team.division_id = division_id if division_id else None
        db.session.commit()
        flash(f"Team '{name}' updated.", "success")
        return redirect(url_for("team_detail", team_id=team_id))

    @app.route("/teams/<int:team_id>/delete", methods=["POST"])
    def delete_team(team_id):
        team = Team.query.get_or_404(team_id)
        team_name = team.name
        db.session.delete(team)
        db.session.commit()
        flash(f"Team '{team_name}' and all associated fixtures have been deleted.", "success")
        return redirect(url_for("list_teams"))

    @app.route("/teams/<int:team_id>")
    def team_detail(team_id):
        team = Team.query.get_or_404(team_id)
        players = team.players.order_by(Player.name).all()
        home_fixtures = team.home_fixtures.order_by(Fixture.date.desc()).all()
        away_fixtures = team.away_fixtures.order_by(Fixture.date.desc()).all()
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
        # Get all players not already on this team for the add player dropdown
        all_players = Player.query.order_by(Player.last_name, Player.first_name).all()

        available_players = [p for p in all_players if p not in players]
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
        )

    # ── Players ─────────────────────────────────────────────────────────

    @app.route("/players/summary")
    def player_summary():
        """Display summary of all club players."""
        players = Player.query.order_by(Player.last_name, Player.first_name).all()

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
                "total_teams": len(player.teams),
                "total_matches": 0,
                "commitments": [],
            }

            teams_dict = {}
            for team in player.teams:
                if team.division and team.division.league:
                    league = team.division.league
                    league_key = league.name
                    if league_key not in teams_dict:
                        teams_dict[league_key] = {"league": league.name, "teams": []}
                    teams_dict[league_key]["teams"].append(team.name)

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
            player_info["total_matches"] = PlayerMatchResult.query.filter_by(
                player_id=player.id
            ).count()
            player_summary_data.append(player_info)

        return render_template(
            "player_summary.html",
            players=player_summary_data,
            total_players=len(player_summary_data),
        )

    @app.route("/players/admin/add-details", methods=["GET", "POST"])
    def admin_add_player_details():
        """Admin form to add club players with the editable/frozen field set."""
        if request.method == "POST":
            first_name = (request.form.get("first_name") or "").strip()
            last_name = (request.form.get("last_name") or "").strip()
            gender = (request.form.get("gender") or "").strip().upper()
            membership_status = (request.form.get("membership_status") or "").strip().lower()
            interest_team_play = (request.form.get("interest_team_play") or "").strip().lower()
            lta_number = (request.form.get("lta_number") or "").strip()

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
            if not lta_number:
                return _invalid("LTA number is required.")

            # Frozen fields: do not trust/consume disabled inputs.
            player = Player(
                first_name=first_name,
                last_name=last_name,
                gender=gender,
                membership_status=membership_status,
                interest_team_play=interest_team_play,
                lta_number=lta_number,
                contact_telephone=None,
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

        team_id = request.form.get("team_id", type=int)

        player_id = request.form.get("player_id", type=int)
        name = request.form.get("name", "").strip()

        if not team_id:
            flash("Team is required.", "error")
            return redirect(url_for("list_teams"))

        team = Team.query.get_or_404(team_id)

        # Case 1: Add existing player from dropdown
        if player_id:
            player = Player.query.get_or_404(player_id)
            if team in player.teams:
                flash(f"Player '{player.name}' is already in {team.name}.", "error")
            else:
                player.teams.append(team)
                db.session.commit()
                flash(f"Player '{player.name}' added to {team.name}.", "success")
        # Case 2: Create new player with name
        elif name:
            player = Player.query.filter_by(name=name).first()
            if not player:
                player = Player(name=name)
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
        results = (
            PlayerMatchResult.query.filter_by(player_id=player.id)
            .join(Rubber)
            .join(Fixture)
            .order_by(Fixture.date.desc())
            .all()
        )
        wins = sum(1 for r in results if r.won)
        losses = sum(1 for r in results if not r.won)
        # Get eligibility info per team/league
        eligibility_info = []
        for team in player.teams:
            if team.division and team.division.league:
                info = check_player_eligibility(player.id, team.id, team.division.league_id)
                info["team"] = team
                info["league"] = team.division.league
                eligibility_info.append(info)
        return render_template(
            "player_detail.html",
            player=player,
            results=results,
            wins=wins,
            losses=losses,
            eligibility_info=eligibility_info,
        )

    @app.route("/players/<int:player_id>/teams/<int:team_id>/remove", methods=["POST"])
    def remove_player_from_team(player_id, team_id):
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
        player = Player.query.get_or_404(player_id)
        if request.method == "POST":
            first_name = (request.form.get("first_name") or "").strip()
            last_name = (request.form.get("last_name") or "").strip()
            gender = (request.form.get("gender") or "").strip().upper()
            membership_status = (request.form.get("membership_status") or "").strip().lower()
            interest_team_play = (request.form.get("interest_team_play") or "").strip().lower()
            lta_number = (request.form.get("lta_number") or "").strip()

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
            if not lta_number:
                return _invalid("LTA number is required.")

            player.first_name = first_name
            player.last_name = last_name
            player.gender = gender
            player.membership_status = membership_status
            player.interest_team_play = interest_team_play
            player.lta_number = lta_number
            db.session.commit()
            flash(f"Player '{player.name}' updated.", "success")
            return redirect(url_for("player_summary"))

        return render_template("edit_player.html", player=player)

    @app.route("/players/<int:player_id>/delete", methods=["POST"])
    def delete_player(player_id):
        player = Player.query.get_or_404(player_id)
        player_name = player.name
        db.session.delete(player)
        db.session.commit()
        flash(f"Player '{player_name}' has been deleted.", "success")
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
            side_lower = side_str.lower()
            return team_lower in side_lower or side_lower in team_lower

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
        teams = Team.query.order_by(Team.name).all()
        leagues = League.query.order_by(League.name).all()

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

            return render_template(
                "import_ics_review.html",
                events=events,
                our_team=our_team,
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
                    our_side = request.form.get(f"our_side_{i}", "home")
                    home_str = request.form.get(f"home_str_{i}", "").strip()
                    away_str = request.form.get(f"away_str_{i}", "").strip()
                    opponent_str = away_str if our_side == "home" else home_str
                    opponent_id = None
                    if opponent_str:
                        opp_lower = opponent_str.lower()
                        for t in Team.query.all():
                            t_lower = t.name.lower()
                            if t_lower == opp_lower or t_lower in opp_lower or opp_lower in t_lower:
                                opponent_id = t.id
                                break
                    home_id = our_team_id if our_side == "home" else opponent_id
                    away_id = our_team_id if our_side == "away" else opponent_id
                    fixture = Fixture(
                        date=fixture_date,
                        home_team_id=home_id,
                        away_team_id=away_id,
                        home_team_name=home_str or None,
                        away_team_name=away_str or None,
                    )
                    db.session.add(fixture)
                    db.session.flush()
                    for rubber_num in range(1, 4):
                        db.session.add(
                            Rubber(
                                fixture_id=fixture.id,
                                rubber_number=rubber_num,
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

    @app.route("/fixtures/manual", methods=["GET", "POST"])
    def manual_fixture_entry():
        """Allow manual entry of fixtures without image parsing."""
        if request.method == "POST":
            try:
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

                # Create fixture
                fixture = Fixture(
                    date=fixture_date,
                    league_id=league_id,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                )
                db.session.add(fixture)
                db.session.flush()

                home_score = 0
                away_score = 0
                eligibility_warnings = []

                # Process rubbers from form
                rubber_count = int(request.form.get("rubber_count", 0))
                for i in range(rubber_count):
                    rubber_num = i + 1
                    rubber_type = request.form.get(f"rubber_{i}_type", "singles")

                    rubber = Rubber(
                        fixture_id=fixture.id,
                        rubber_number=rubber_num,
                        rubber_type=rubber_type,
                    )

                    # Get set scores
                    try:
                        rubber.home_set1 = int(request.form.get(f"rubber_{i}_home_set1") or 0)
                        rubber.away_set1 = int(request.form.get(f"rubber_{i}_away_set1") or 0)
                        rubber.home_set2 = int(request.form.get(f"rubber_{i}_home_set2") or 0)
                        rubber.away_set2 = int(request.form.get(f"rubber_{i}_away_set2") or 0)
                        rubber.home_set3 = int(request.form.get(f"rubber_{i}_home_set3") or 0)
                        rubber.away_set3 = int(request.form.get(f"rubber_{i}_away_set3") or 0)
                    except (ValueError, TypeError):
                        pass

                    # Determine winner from sets
                    home_sets = sum(
                        1
                        for s1, s2 in [
                            (rubber.home_set1, rubber.away_set1),
                            (rubber.home_set2, rubber.away_set2),
                            (rubber.home_set3, rubber.away_set3),
                        ]
                        if s1 and s2 and s1 > s2
                    )
                    away_sets = sum(
                        1
                        for s1, s2 in [
                            (rubber.home_set1, rubber.away_set1),
                            (rubber.home_set2, rubber.away_set2),
                            (rubber.home_set3, rubber.away_set3),
                        ]
                        if s2 and s1 and s2 > s1
                    )

                    if home_sets > away_sets:
                        rubber.winner = "home"
                        home_score += 1
                    elif away_sets > home_sets:
                        rubber.winner = "away"
                        away_score += 1

                    db.session.add(rubber)
                    db.session.flush()

                    # Add home players
                    home_players_str = request.form.get(f"rubber_{i}_home_players", "")
                    for player_name in [
                        p.strip() for p in home_players_str.split(",") if p.strip()
                    ]:
                        player = _get_or_create_player(player_name, home_team.id)
                        if league_id:
                            elig = check_player_eligibility(player.id, home_team.id, league_id)
                            if not elig["eligible"]:
                                eligibility_warnings.append(
                                    f"⚠️ {player_name} is already committed to a "
                                    f"different team in this league"
                                )
                        result = PlayerMatchResult(
                            player_id=player.id,
                            rubber_id=rubber.id,
                            side="home",
                            won=(rubber.winner == "home"),
                        )
                        db.session.add(result)

                    # Add away players
                    away_players_str = request.form.get(f"rubber_{i}_away_players", "")
                    for player_name in [
                        p.strip() for p in away_players_str.split(",") if p.strip()
                    ]:
                        player = _get_or_create_player(player_name, away_team.id)
                        if league_id:
                            elig = check_player_eligibility(player.id, away_team.id, league_id)
                            if not elig["eligible"]:
                                eligibility_warnings.append(
                                    "⚠️ %s is already committed to a different team in "
                                    "this league" % player_name
                                )
                        result = PlayerMatchResult(
                            player_id=player.id,
                            rubber_id=rubber.id,
                            side="away",
                            won=(rubber.winner == "away"),
                        )
                        db.session.add(result)

                fixture.home_score = home_score
                fixture.away_score = away_score
                db.session.commit()

                # Check for new team commitments
                if league_id:
                    restriction = LeagueRestriction.query.filter_by(league_id=league_id).first()
                    if restriction and restriction.team_commitment_threshold > 0:
                        all_players = set()
                        for i in range(rubber_count):
                            home_str = request.form.get(f"rubber_{i}_home_players", "")
                            away_str = request.form.get(f"rubber_{i}_away_players", "")
                            all_players.update(
                                [p.strip() for p in home_str.split(",") if p.strip()]
                            )
                            all_players.update(
                                [p.strip() for p in away_str.split(",") if p.strip()]
                            )

                        for player_name in all_players:
                            player = Player.query.filter_by(name=player_name).first()
                            if not player:
                                continue

                            for team in [home_team, away_team]:
                                elig = check_player_eligibility(player.id, team.id, league_id)
                                fixture_count = elig["current_fixtures"]

                                if fixture_count >= restriction.team_commitment_threshold:
                                    existing_commitment = PlayerTeamCommitment.query.filter_by(
                                        player_id=player.id,
                                        league_id=league_id,
                                        season="2026",
                                    ).first()
                        if not existing_commitment:
                            commitment = PlayerTeamCommitment(
                                player_id=player.id,
                                team_id=team.id,
                                league_id=league_id,
                                season="2026",
                            )
                            db.session.add(commitment)
                            eligibility_warnings.append(
                                f"✅ {player_name} committed to {team.name} " "for the season!"
                            )

                        db.session.commit()

                if eligibility_warnings:
                    for warning in eligibility_warnings:
                        flash(warning, "warning")

                flash(
                    f"Fixture saved: {home_team.name} {home_score} – {away_score} "
                    f"{away_team.name}",
                    "success",
                )
                return redirect(url_for("fixture_detail", fixture_id=fixture.id))

            except Exception as e:
                flash(f"Error saving fixture: {str(e)}", "error")
                return redirect(url_for("manual_fixture_entry"))

        # GET: Show form
        teams = Team.query.order_by(Team.name).all()
        leagues = League.query.order_by(League.name).all()
        return render_template(
            "manual_fixture_entry.html",
            teams=teams,
            leagues=leagues,
        )

    @app.route("/fixtures/<int:fixture_id>")
    def fixture_detail(fixture_id):
        fixture = Fixture.query.get_or_404(fixture_id)
        rubbers = fixture.rubbers.order_by(Rubber.rubber_number).all()
        leagues = League.query.order_by(League.name).all()
        return render_template(
            "fixture_detail.html", fixture=fixture, rubbers=rubbers, leagues=leagues
        )

    @app.route("/fixtures/<int:fixture_id>/edit", methods=["POST"])
    def edit_fixture(fixture_id):
        fixture = Fixture.query.get_or_404(fixture_id)
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
            fixture.home_score = request.form.get("home_score", 0, type=int)
            fixture.away_score = request.form.get("away_score", 0, type=int)
            db.session.commit()
            flash("Fixture updated.", "success")
            return redirect(url_for("fixture_detail", fixture_id=fixture_id))
        except Exception as e:
            flash(f"Error updating fixture: {str(e)}", "error")
            return redirect(url_for("fixture_detail", fixture_id=fixture_id))

    @app.route("/fixtures/bulk-delete", methods=["POST"])
    def bulk_delete_fixtures():
        """Delete multiple fixtures by ID."""
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

    @app.route("/fixtures/<int:fixture_id>/delete", methods=["POST"])
    def delete_fixture(fixture_id):
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
        fixtures = Fixture.query.order_by(Fixture.date.asc()).all()
        return render_template("fixtures.html", fixtures=fixtures)

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
        results = PlayerMatchResult.query.filter_by(player_id=player.id).all()
        wins = sum(1 for r in results if r.won)
        losses = sum(1 for r in results if not r.won)
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

    def _get_or_create_player(name, team_id):
        """Find or create a player and add them to the team if not already on it."""
        player = Player.query.filter_by(name=name).first()
        if not player:
            player = Player(name=name)
            db.session.add(player)
            db.session.flush()
        # Ensure player is on the team
        team = Team.query.get(team_id)
        if team and team not in player.teams:
            player.teams.append(team)
        return player

    return app
