"""Flask application factory and route definitions."""

import os
from datetime import datetime

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

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

    # ── Leagues ─────────────────────────────────────────────────────────

    @app.route("/leagues")
    def list_leagues():
        leagues = League.query.order_by(League.name).all()
        return render_template("leagues.html", leagues=leagues)

    @app.route("/leagues", methods=["POST"])
    def add_league():
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        max_matches = request.form.get("max_matches_per_team", type=int)
        if not name:
            flash("League name is required.", "error")
            return redirect(url_for("list_leagues"))
        if League.query.filter_by(name=name).first():
            flash(f"League '{name}' already exists.", "error")
            return redirect(url_for("list_leagues"))
        league = League(name=name, description=description or None)
        db.session.add(league)
        db.session.flush()
        # Create restriction if specified
        if max_matches and max_matches > 0:
            restriction = LeagueRestriction(
                league_id=league.id,
                max_matches_per_team=max_matches,
                description=f"Maximum {max_matches} matches per team in {name}",
            )
            db.session.add(restriction)
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

        if not name:
            flash("League name is required.", "error")
            return redirect(url_for("league_detail", league_id=league_id))

        # Check if new name conflicts with another league
        if name != league.name and League.query.filter_by(name=name).first():
            flash(f"League '{name}' already exists.", "error")
            return redirect(url_for("league_detail", league_id=league_id))

        league.name = name
        league.description = description or None
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
        fixtures = Fixture.query.order_by(Fixture.date.desc()).all()
        teams = Team.query.order_by(Team.name).all()
        leagues = League.query.order_by(League.name).all()
        return render_template("index.html", fixtures=fixtures, teams=teams, leagues=leagues)

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
        all_players = Player.query.order_by(Player.name).all()
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
        """Display summary of all players' weekly commitment across teams and seasons."""
        players = Player.query.order_by(Player.name).all()

        # Build player commitment data
        player_summary_data = []
        for player in players:
            player_info = {
                "id": player.id,
                "name": player.name,
                "teams": [],
                "total_teams": len(player.teams),
                "total_matches": 0,
                "commitments": [],
            }

            # Get teams and their leagues
            teams_dict = {}
            for team in player.teams:
                if team.division and team.division.league:
                    league = team.division.league
                    league_key = f"{league.name}"
                    if league_key not in teams_dict:
                        teams_dict[league_key] = {"league": league.name, "teams": []}
                    teams_dict[league_key]["teams"].append(team.name)

                    # Get commitment info
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

            # Get match count
            match_count = PlayerMatchResult.query.filter_by(player_id=player.id).count()
            player_info["total_matches"] = match_count

            if player_info["teams"] or player_info["commitments"]:
                player_summary_data.append(player_info)

        return render_template(
            "player_summary.html",
            players=player_summary_data,
            total_players=len(player_summary_data),
        )

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

    @app.route("/players/<int:player_id>/delete", methods=["POST"])
    def delete_player(player_id):
        player = Player.query.get_or_404(player_id)
        player_name = player.name
        db.session.delete(player)
        db.session.commit()
        flash(f"Player '{player_name}' has been deleted.", "success")
        return redirect(url_for("player_summary"))

    # ── Fixtures ────────────────────────────────────────────────────────

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
            db.session.commit()
            flash("Fixture updated.", "success")
            return redirect(url_for("fixture_detail", fixture_id=fixture_id))
        except Exception as e:
            flash(f"Error updating fixture: {str(e)}", "error")
            return redirect(url_for("fixture_detail", fixture_id=fixture_id))

    @app.route("/fixtures/<int:fixture_id>/delete", methods=["POST"])
    def delete_fixture(fixture_id):
        fixture = Fixture.query.get_or_404(fixture_id)
        home_team_name = fixture.home_team.name
        away_team_name = fixture.away_team.name
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
        fixtures = Fixture.query.order_by(Fixture.date.desc()).all()
        leagues = League.query.order_by(League.name).all()
        return render_template("fixtures.html", fixtures=fixtures, leagues=leagues)

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
